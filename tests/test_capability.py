"""MCP capability: can the Paytm MCP tools solve the scenario, and the need_human flag on clarity responses."""

import pytest

from mappa import autopilot, capability, clarify, pipeline, policies, sessions, store
from mappa.schema import Details, Party, Ticket, TicketIn, now

PARTY = Party(role="merchant", name="Amal Medicos", phone="9876500002", merchant_id="M2002")


def _reply(category, details, conclusion="Resolved under policy", subject="Support request"):
    return {"clarification_needed": False, "clarifications": [], "conclusion": conclusion, "confidence": 0.9,
            "critical": False, "critical_reason": None,
            "ticket": {"category": category, "subject": subject, "ask": "fix it", "details": details,
                       "caller_claims": [], "human_requested": False}}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "PATH", tmp_path / "clarity_sessions.json")
    monkeypatch.setattr(store, "PATH", tmp_path / "tickets.json")

    class NoTree:
        def search(self, q, cat, k=3):
            return []

    monkeypatch.setattr(policies, "tree", lambda: NoTree())
    monkeypatch.setattr(policies, "triggered", lambda text, cat: [])
    status = {"value": "closed"}

    def fake_raise(body: TicketIn) -> Ticket:  # stands in for clarity -> Jev -> autopilot
        auto = {"outcome": "resolved", "result": "done"} if status["value"] == "closed" else None
        t = Ticket(**body.model_dump(), id="TKT-000001", created_at=now(), updated_at=now(), status=status["value"],
                   autopilot=auto)
        return store.save(t)

    monkeypatch.setattr(pipeline, "raise_ticket", fake_raise)
    return status


def step(info, reply):
    s = sessions.create(channel="call", raised_by=PARTY)
    return clarify.step(s.id, info, llm=lambda messages: reply)


# ---------------- capability.check against the dummy Paytm actions MCP server ----------------

def test_refund_with_txn_is_solvable_over_mcp():
    c = capability.check("TRACE_REFUND", Details(amount=1200, refund_kind="not received", txn_id="T88213"))
    assert c["solvable"] and not c["need_human"] and c["server"] and "get_refund" in c["tools_required"]


def test_refund_without_txn_is_automatable_but_needs_the_detail():
    c = capability.check("TRACE_REFUND", Details(amount=1200, refund_kind="not received"))
    assert c["automatable"] and not c["solvable"] and not c["need_human"] and c["details_missing"] == ["txn_id"]


def test_no_playbook_needs_a_human():
    c = capability.check("TRACE_PAYMENT", Details(amount=560, txn_id="T1"))
    assert c["need_human"] and not c["playbook"]


def test_tool_missing_on_the_server_needs_a_human(monkeypatch):
    served = {k: v for k, v in autopilot.CLIENT.list_tools().items() if k != "initiate_refund"}
    monkeypatch.setattr(autopilot.CLIENT, "list_tools", lambda: served)
    c = capability.check("INITIATE_REFUND", Details(amount=500, refund_kind="new request", txn_id="T1"))
    assert c["need_human"] and c["tools_missing"] == ["initiate_refund"]


def test_server_down_needs_a_human(monkeypatch):
    def down():
        raise ConnectionError("no server")
    monkeypatch.setattr(autopilot.CLIENT, "list_tools", down)
    c = capability.check("DIAGNOSE_DEVICE", Details(device_issue="offline", offline_days=2))
    assert c["need_human"] and "unavailable" in c["reason"]


# ---------------- need_human on clarity responses ----------------

def test_solvable_scenario_concludes_with_need_human_false():
    out = step("Soundbox 3 din se band hai", _reply("device", {"device_issue": "offline", "offline_days": 3}))
    assert out["status"] == "done" and out["need_human"] is False and out["mcp"]["solvable"]


def test_unsolvable_scenario_escalates_with_need_human_true():
    out = step("560 rupaye kat gaye, payment fail", _reply("payment", {"amount": 560, "txn_id": "T99"}))
    assert out["status"] == "escalate" and out["need_human"] is True
    assert "not solvable with the Paytm MCP tools" in out["need_human_reason"]


def test_missing_mcp_detail_asks_instead_of_concluding():
    out = step("customer ka refund nahi mila", _reply("refund", {"amount": 1200, "refund_kind": "not received"}))
    assert out["status"] == "needs_clarification" and out["need_human"] is False
    assert any("transaction ID" in q for q in out["clarifications"])


def test_need_human_is_decided_by_clarity_not_by_the_ticket_route(env):
    env["value"] = "human_queue"  # whatever Jev / the autopilot do with the ticket afterwards
    out = step("Soundbox band hai", _reply("device", {"device_issue": "offline", "offline_days": 3}))
    assert out["status"] == "done" and out["need_human"] is False and out["ticket_status"] == "human_queue"


def test_conclusion_without_a_category_needs_a_human():
    out = step("kuch problem hai", _reply(None, {}))
    assert out["status"] == "done" and out["need_human"] is True
