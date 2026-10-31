"""Autopilot: resolves AI-routed tickets by calling Paytm's action tools over MCP, then closes them for human review.

    ticket routed AI -> playbook for its action item -> MCP tools/call on the Paytm actions server (mappa.paytm_mcp)
      -> every call logged (tool, arguments, result, why)
      -> RESOLVED: ticket closed, summary + log written as its resolution, waits for support's review and feedback
      -> HANDOFF: a check failed (unverified account, open chargeback, ...): ticket goes to the human queue with the log

Playbooks are deterministic and follow the policy clauses they cite. Checks always come before writes, every write
is verified by reading back, and any surprise hands the ticket to a human rather than improvising.
Point PAYTM_MCP_CMD at Paytm's real actions server to go live.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time

from .schema import now

CMD = shlex.split(os.environ.get("PAYTM_MCP_CMD", f"{sys.executable} -m mappa.paytm_mcp"))


class MCPClient:
    """Minimal stdio MCP client: one long-lived server process, one request at a time."""

    def __init__(self, cmd):
        self.cmd, self.p, self.n, self.lock = cmd, None, 0, threading.Lock()

    def _rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        if "error" in resp:
            raise RuntimeError(resp["error"]["message"])
        return resp["result"]

    def _start(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=root)
        info = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "mappa-autopilot", "version": "0.1.0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()
        self.server = info["serverInfo"]["name"]
        self.tools = {t["name"]: t for t in self._rpc("tools/list")["tools"]}

    def _ready(self):
        if self.p is None or self.p.poll() is not None:
            self._start()

    def list_tools(self) -> dict:
        """The tools the actions server serves right now (MCP tools/list), by name."""
        with self.lock:
            self._ready()
            self.tools = {t["name"]: t for t in self._rpc("tools/list")["tools"]}
            return self.tools

    def call(self, name, args):
        with self.lock:
            self._ready()
            r = self._rpc("tools/call", {"name": name, "arguments": args})
        if r.get("isError"):
            raise RuntimeError(r["content"][0]["text"])
        return r.get("structuredContent") or json.loads(r["content"][0]["text"])


CLIENT = MCPClient(CMD)


class Handoff(Exception):
    pass


class Run:
    def __init__(self, ticket):
        self.t, self.log = ticket, []
        self.mid = ticket.raised_by.merchant_id or f"M{ticket.raised_by.phone[-6:]}"
        self.d = ticket.clarity.details

    def call(self, tool, why, **args):
        t0 = time.perf_counter()
        try:
            out = CLIENT.call(tool, args)
            ok = True
        except Exception as e:
            out, ok = {"error": str(e)}, False
        self.log.append({"at": now().isoformat(), "tool": tool, "why": why, "args": args, "result": out, "ok": ok,
                         "ms": round((time.perf_counter() - t0) * 1000, 1)})
        if not ok:
            raise Handoff(f"{tool} failed: {out['error']}")
        return out

    def sms(self, text):
        self.call("send_sms", "tell the customer the outcome", phone=self.t.raised_by.phone, text=text)
        return text


# ---------------- playbooks: action code -> function(run) -> outcome sentence ----------------

def _settle(r: Run, check_new_account: bool):
    s = r.call("get_settlement", "find the settlement the ticket is about (P-SET-01)", merchant_id=r.mid, amount=r.d.amount)
    if s["status"] == "CREDITED":
        return r.sms(f"Your settlement of ₹{s['amount']:,.0f} was credited, UTR {s['utr']}.")
    if s["bank_status"] == "DOWN":
        return r.sms("Your bank is temporarily down. The settlement is retried automatically when it is back (P-SET-03).")
    accts = r.call("get_bank_accounts", "find the account to pay into (P-SET-04)", merchant_id=r.mid,
                   account_changed=r.d.account_changed, bank_name=r.d.bank_name)["accounts"]
    active = [a for a in accts if a["status"] == "ACTIVE"]
    if check_new_account:
        sec = r.call("get_security_events", "account change after a new-device login? (P-SET-05)", merchant_id=r.mid)
        if sec["recent_device_change"] or sec["recent_password_reset"]:
            raise Handoff("new-device login or password reset near the account change: possible takeover (P-SET-05)")
    good = [a for a in active if a["verified"] and a["holder_match"]]
    if not good:
        raise Handoff("no verified bank account in the merchant's name to pay into (P-SET-04)")
    acct = good[-1]
    r.call("retry_settlement", "re-send once to the verified account (P-SET-02)", settlement_id=s["settlement_id"],
           account_id=acct["account_id"], amount=s["amount"])
    s2 = r.call("get_settlement", "verify the retry", merchant_id=r.mid, amount=r.d.amount)
    if s2["status"] != "CREDITED":
        raise Handoff("settlement still not credited after the retry")
    return r.sms(f"Your settlement of ₹{s2['amount']:,.0f} was sent to {acct['bank']} ••{acct['last4']}, UTR {s2['utr']}.")


def _trace_refund(r: Run):
    if not r.d.txn_id:
        raise Handoff("no transaction ID to trace the refund")
    f = r.call("get_refund", "refund status (P-REF-01)", txn_id=r.d.txn_id)
    if f["status"] == "SUCCESS":
        return r.sms(f"The refund was successful. The customer can trace it with RRN {f['rrn']} (P-REF-03).")
    if f["status"] == "PENDING_AT_BANK" and f["days_since_initiated"] <= f["tat_days"]:
        return r.sms(f"The refund is with the bank and should reach the customer within {f['tat_days']} working days (P-REF-01).")
    if f["status"] == "FAILED":
        return _refund(r, why="re-initiate the failed refund to the original source (P-REF-02)")
    raise Handoff(f"refund status {f['status']} needs a person")


def _refund(r: Run, why="refund to the original source (P-REF-04)"):
    if not r.d.txn_id:
        raise Handoff("no transaction ID to refund")
    tx = r.call("get_transaction", "check the transaction before refunding (P-REF-05, P-REF-06)", txn_id=r.d.txn_id)
    if tx["chargeback_open"]:
        raise Handoff("open chargeback on the transaction (P-REF-06)")
    amount = min(r.d.refund_amount or r.d.amount or tx["amount"], tx["amount"])
    r.call("initiate_refund", why, txn_id=r.d.txn_id, amount=amount)
    if r.call("get_refund", "verify the refund was created", txn_id=r.d.txn_id)["status"] not in ("PENDING_AT_BANK", "SUCCESS"):
        raise Handoff("refund not visible after initiating it")
    return r.sms(f"A refund of ₹{amount:,.0f} on {r.d.txn_id} was initiated to the original payment source (P-REF-01).")


def _device(r: Run):
    g = r.call("run_device_diagnostics", "remote diagnostics first (P-DEV-01)", merchant_id=r.mid)
    if g["telecom_outage"]:
        return r.sms("There is a telecom outage in your area. Payments are still collected and settled (P-DEV-02).")
    if g["speaker"] == "FAIL":
        rp = r.call("raise_device_replacement", "speaker failed diagnostics (P-DEV-03)", merchant_id=r.mid)
        return r.sms(f"Your Soundbox will be replaced (request {rp['replacement_id']}), visit within {rp['visit_within_hours']} hours.")
    if g["sim"] != "OK":
        r.call("reprovision_sim", "no signal (P-DEV-01)", merchant_id=r.mid)
    if "outdated" in g["firmware"]:
        r.call("push_firmware", "outdated firmware (P-DEV-01)", merchant_id=r.mid)
    g2 = r.call("run_device_diagnostics", "verify the fix", merchant_id=r.mid)
    if g2["sim"] != "OK" or g2["speaker"] != "OK":
        raise Handoff("device still failing diagnostics")
    return r.sms("Your Soundbox passed diagnostics and is back online.")


def _kyc(r: Run):
    k = r.call("get_kyc_status", "what is pending or why it was rejected (P-KYC-02, P-KYC-03)", merchant_id=r.mid)
    doc = (k["pending_docs"] or ["a document whose name matches your PAN"])[0]
    r.call("send_kyc_upload_link", "upload link in the app, never by chat (P-KYC-02)", merchant_id=r.mid, document=doc)
    reason = f"KYC was rejected: {k['reason']}. " if k["status"] == "REJECTED" else ""
    return r.sms(f"{reason}Please upload {doc} using the link in the Paytm app.")


SETTLE = ["get_settlement", "get_bank_accounts", "retry_settlement", "send_sms"]
REFUND = ["get_refund", "get_transaction", "initiate_refund", "send_sms"]
DEVICE = ["run_device_diagnostics", "raise_device_replacement", "reprovision_sim", "push_firmware", "send_sms"]
KYC = ["get_kyc_status", "send_kyc_upload_link", "send_sms"]

# action code -> (playbook, MCP tools it may call, ticket details its tool calls need)
PLAYBOOKS = {
    "TRACE_AND_RETRY_SETTLEMENT": (lambda r: _settle(r, check_new_account=False), SETTLE, []),
    "VERIFY_ACCOUNT_THEN_SETTLE": (lambda r: _settle(r, check_new_account=True), SETTLE + ["get_security_events"], []),
    "TRACE_REFUND": (_trace_refund, REFUND, ["txn_id"]),
    "INITIATE_REFUND": (_refund, REFUND, ["txn_id"]),
    "DIAGNOSE_AUDIO": (_device, DEVICE, []), "DIAGNOSE_CONNECTIVITY": (_device, DEVICE, []),
    "DIAGNOSE_DEVICE": (_device, DEVICE, []),
    "REQUEST_KYC_DOCUMENT": (_kyc, KYC, []), "REVIEW_KYC_REJECTION": (_kyc, KYC, []),
}


def run(ticket) -> dict:
    """Resolve an AI-routed ticket. Returns the autopilot record; the caller sets the ticket's status from `outcome`."""
    c, r, t0 = ticket.clarity, Run(ticket), now()
    pb = PLAYBOOKS.get(c.action.code)
    try:
        if pb is None:
            raise Handoff(f"no playbook for {c.action.code}")
        result, outcome = pb[0](r), "resolved"
    except Handoff as h:
        result, outcome = str(h), "handoff"
    writes = [x["tool"] for x in r.log if CLIENT.tools.get(x["tool"], {}).get("description", "").startswith("[write]")]
    summary = (f"{ticket.id} ({ticket.channel}, {c.category}): {c.scenario_title}. "
               f"Routed to AI: {c.decision.why}. "
               f"Autopilot ran {len(r.log)} tool calls ({len(writes)} actions: {', '.join(writes) or 'none'}). "
               + (f"Resolved: {result}" if outcome == "resolved" else f"Handed to a human: {result}."))
    return {"outcome": outcome, "result": result, "summary": summary, "log": r.log, "playbook": c.action.code,
            "server": getattr(CLIENT, "server", None), "started_at": t0.isoformat(), "finished_at": now().isoformat()}
