"""Paytm actions MCP server: the tools the autopilot calls to resolve AI-routed tickets.   python -m mappa.paytm_mcp

DUMMY FUNCTIONS. In production Paytm exposes its real, actionable APIs here (settlements, refunds, devices, KYC, SMS)
with the same tool names and schemas; nothing else changes. The dummies return deterministic, plausible records
(seeded by merchant / transaction ID), so the same ticket always produces the same run, and keep writes in memory.

Speaks MCP over stdio (JSON-RPC 2.0, one message per line), the same protocol as mappa.mcp_server.
"""

import hashlib
import json
import random
import sys

PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


class RpcError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def _rng(*keys) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, keys)).encode()).hexdigest()[:12], 16))


WRITES: dict[str, dict] = {}   # in-memory effects of write tools: {key: result}
BANKS = ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank", "Kotak", "Bank of Baroda"]


# ---------------- read tools ----------------

def get_settlement(merchant_id: str, amount: float | None = None) -> dict:
    r = _rng("settle", merchant_id, amount)
    sid = f"S{r.randint(10000, 99999)}"
    if sid in WRITES:
        return WRITES[sid]
    status = r.choices(["FAILED", "CREDITED"], [0.85, 0.15])[0]
    return {"settlement_id": sid, "amount": amount or r.randint(500, 40000), "status": status,
            "failure_code": r.choice(["ACCOUNT_CLOSED", "INVALID_IFSC", "BANK_TIMEOUT"]) if status == "FAILED" else None,
            "bank_status": r.choices(["UP", "DOWN"], [0.9, 0.1])[0], "utr": None if status == "FAILED" else f"UTR{r.randint(10**9, 10**10)}"}


def get_bank_accounts(merchant_id: str, account_changed: bool | None = None, bank_name: str | None = None) -> dict:
    r = _rng("acct", merchant_id)
    accts = [{"account_id": f"A{r.randint(100, 999)}", "bank": r.choice(BANKS), "last4": f"{r.randint(0, 9999):04d}",
              "status": "ACTIVE", "verified": True, "holder_match": True, "added_hours_ago": r.randint(2000, 20000)}]
    if account_changed:
        accts[0]["status"] = "CLOSED"
        accts.append({"account_id": f"A{r.randint(100, 999)}", "bank": bank_name or r.choice(BANKS),
                      "last4": f"{r.randint(0, 9999):04d}", "status": "ACTIVE", "verified": r.random() < 0.6,
                      "holder_match": r.random() < 0.9, "added_hours_ago": r.randint(3, 400)})
    return {"accounts": accts}


def get_security_events(merchant_id: str) -> dict:
    r = _rng("sec", merchant_id)
    return {"recent_device_change": r.random() < 0.2, "recent_password_reset": r.random() < 0.1}


def get_transaction(txn_id: str) -> dict:
    r = _rng("txn", txn_id)
    return {"txn_id": txn_id, "amount": r.randint(100, 20000), "status": "SUCCESS", "source": "UPI",
            "chargeback_open": r.random() < 0.08}


def get_refund(txn_id: str) -> dict:
    key = f"refund:{txn_id}"
    if key in WRITES:
        return WRITES[key]
    r = _rng("refund", txn_id)
    status = r.choices(["SUCCESS", "PENDING_AT_BANK", "FAILED", "NONE"], [0.35, 0.35, 0.2, 0.1])[0]
    return {"txn_id": txn_id, "status": status, "rrn": f"RRN{r.randint(10**9, 10**10)}" if status == "SUCCESS" else None,
            "days_since_initiated": r.randint(1, 3) if status != "NONE" else None, "tat_days": 3}


def run_device_diagnostics(merchant_id: str) -> dict:
    key = f"device:{merchant_id}"
    if key in WRITES:
        return WRITES[key]
    r = _rng("dev", merchant_id)
    return {"device_id": f"SB{r.randint(10000, 99999)}", "speaker": r.choices(["OK", "FAIL"], [0.7, 0.3])[0],
            "sim": r.choices(["OK", "NO_SIGNAL"], [0.6, 0.4])[0], "firmware": r.choice(["4.2.1 (latest)", "3.9.0 (outdated)"]),
            "telecom_outage": r.random() < 0.1}


def get_kyc_status(merchant_id: str) -> dict:
    r = _rng("kyc", merchant_id)
    status = r.choice(["PENDING", "REJECTED"])
    return {"status": status, "pending_docs": [r.choice(["PAN", "address proof", "GST certificate"])] if status == "PENDING" else [],
            "reason": r.choice(["name on PAN does not match the business name", "address proof is unreadable"]) if status == "REJECTED" else None}


# ---------------- write tools ----------------

def retry_settlement(settlement_id: str, account_id: str, amount: float) -> dict:
    res = {"settlement_id": settlement_id, "amount": amount, "status": "CREDITED", "account_id": account_id,
           "utr": f"UTR{_rng('utr', settlement_id).randint(10**9, 10**10)}", "bank_status": "UP", "failure_code": None}
    WRITES[settlement_id] = res
    return {"ok": True, "settlement_id": settlement_id, "status": "INITIATED"}


def initiate_refund(txn_id: str, amount: float) -> dict:
    WRITES[f"refund:{txn_id}"] = {"txn_id": txn_id, "status": "PENDING_AT_BANK", "rrn": None, "days_since_initiated": 0,
                                  "tat_days": 3, "amount": amount}
    return {"ok": True, "refund_id": f"R{_rng('rid', txn_id).randint(10000, 99999)}", "status": "INITIATED"}


def _device_fixed(merchant_id, **fix):
    d = run_device_diagnostics(merchant_id) | fix
    WRITES[f"device:{merchant_id}"] = d
    return {"ok": True}


def reprovision_sim(merchant_id: str) -> dict:
    return _device_fixed(merchant_id, sim="OK")


def push_firmware(merchant_id: str) -> dict:
    return _device_fixed(merchant_id, firmware="4.2.1 (latest)")


def raise_device_replacement(merchant_id: str) -> dict:
    return {"ok": True, "replacement_id": f"RPL{_rng('rpl', merchant_id).randint(1000, 9999)}", "visit_within_hours": 48}


def send_kyc_upload_link(merchant_id: str, document: str) -> dict:
    return {"ok": True, "document": document, "link_sent": True}


def send_sms(phone: str, text: str) -> dict:
    return {"ok": True, "to": phone[-4:].rjust(len(phone), "•"), "chars": len(text)}


def _tool(fn, desc, props, kind):
    return {"fn": fn, "kind": kind, "spec": {"name": fn.__name__, "description": f"[{kind}] {desc}",
            "inputSchema": {"type": "object", "properties": props, "required": [k for k, v in props.items() if not v.pop("optional", False)]}}}


S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
TOOLS = {t["spec"]["name"]: t for t in [
    _tool(get_settlement, "Latest settlement for a merchant", {"merchant_id": S, "amount": N | {"optional": True}}, "read"),
    _tool(get_bank_accounts, "Bank accounts on file", {"merchant_id": S, "account_changed": B | {"optional": True},
                                                        "bank_name": S | {"optional": True}}, "read"),
    _tool(get_security_events, "Recent device change / password reset", {"merchant_id": S}, "read"),
    _tool(get_transaction, "A payment transaction", {"txn_id": S}, "read"),
    _tool(get_refund, "Refund status for a transaction", {"txn_id": S}, "read"),
    _tool(run_device_diagnostics, "Remote Soundbox diagnostics", {"merchant_id": S}, "read"),
    _tool(get_kyc_status, "KYC status", {"merchant_id": S}, "read"),
    _tool(retry_settlement, "Re-send a failed settlement to a verified account", {"settlement_id": S, "account_id": S, "amount": N}, "write"),
    _tool(initiate_refund, "Refund a transaction to its original source", {"txn_id": S, "amount": N}, "write"),
    _tool(reprovision_sim, "Re-provision the Soundbox SIM", {"merchant_id": S}, "write"),
    _tool(push_firmware, "Push the latest Soundbox firmware", {"merchant_id": S}, "write"),
    _tool(raise_device_replacement, "Raise a Soundbox replacement", {"merchant_id": S}, "write"),
    _tool(send_kyc_upload_link, "Send the in-app upload link for a KYC document", {"merchant_id": S, "document": S}, "write"),
    _tool(send_sms, "SMS the customer", {"phone": S, "text": S}, "write"),
]}


def handle(msg: dict) -> dict | None:
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:
        return None
    if method == "initialize":
        v = params.get("protocolVersion")
        result = {"protocolVersion": v if v in PROTOCOLS else "2024-11-05", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "paytm-actions (dummy)", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [t["spec"] for t in TOOLS.values()]}
    elif method == "tools/call":
        t = TOOLS.get(params.get("name"))
        if not t:
            raise RpcError(-32602, f"Unknown tool: {params.get('name')}")
        try:
            out = t["fn"](**(params.get("arguments") or {}))
            result = {"content": [{"type": "text", "text": json.dumps(out)}], "structuredContent": out}
        except TypeError as e:
            result = {"content": [{"type": "text", "text": f"bad arguments: {e}"}], "isError": True}
    else:
        raise RpcError(-32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        try:
            resp = handle(msg)
        except RpcError as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": e.code, "message": e.message}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
