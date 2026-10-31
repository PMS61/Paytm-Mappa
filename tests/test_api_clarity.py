import pytest
from fastapi.testclient import TestClient

from mappa import api, policies, sarvam, sessions, store
from mappa.sarvam import SarvamError

client = TestClient(api.app)
PARTY = {"role": "merchant", "name": "Amal Medicos", "phone": "9876500002", "merchant_id": "M2002"}
ASKING = {"clarification_needed": True, "clarifications": ["Which transaction ID?"], "conclusion": None,
          "confidence": 0.3, "critical": False, "critical_reason": None, "ticket": {}}
DONE = {"clarification_needed": False, "clarifications": [], "conclusion": "Refund approved under P-REF-01",
        "confidence": 0.9, "critical": False, "critical_reason": None,
        "ticket": {"category": "refund", "subject": "Refund 4000 to customer", "ask": "refund 4000",
                   "details": {"amount": 4000, "refund_kind": "new request", "txn_id": "T5512"},
                   "caller_claims": [], "human_requested": False}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "PATH", tmp_path / "clarity_sessions.json")
    monkeypatch.setattr(store, "PATH", tmp_path / "tickets.json")
    monkeypatch.delenv("MAPPA_CLARITY_MAX_CALLS", raising=False)
    monkeypatch.setattr(policies, "triggered", lambda text, cat: [])
    return tmp_path


def fake_llm(monkeypatch, reply):
    calls = []

    def chat(messages):
        calls.append(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(sarvam, "chat", chat)
    return calls


def _start() -> str:
    r = client.post("/api/clarity/sessions", json={"channel": "call", "raised_by": PARTY})
    assert r.status_code == 200
    return r.json()["id"]


def test_start_session_returns_id_and_get_returns_session():
    sid = _start()
    assert sid.startswith("CLR-")
    r = client.get(f"/api/clarity/{sid}")
    assert r.status_code == 200 and r.json()["id"] == sid and r.json()["status"] == "open"


def test_start_session_validation_error_still_has_id_key():
    r = client.post("/api/clarity/sessions", json={"channel": "email", "raised_by": PARTY})
    assert r.status_code == 422 and r.json()["id"] is None and "detail" in r.json()


def test_clarity_turn_needs_clarification(monkeypatch):
    fake_llm(monkeypatch, ASKING)
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": "I want a refund"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid and body["status"] == "needs_clarification" and body["done"] is False
    assert body["clarifications"] == ["Which transaction ID?"] and body["calls_left"] == 4


def test_unknown_session_is_404_with_id(monkeypatch):
    fake_llm(monkeypatch, ASKING)
    r = client.post("/api/clarity", json={"id": "CLR-nope0000", "information": "hi"})
    assert r.status_code == 404 and r.json()["id"] == "CLR-nope0000"
    r = client.get("/api/clarity/CLR-nope0000")
    assert r.status_code == 404 and r.json()["id"] == "CLR-nope0000"


def test_empty_information_is_422_with_id():
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": ""})
    assert r.status_code == 422 and r.json()["id"] == sid and "detail" in r.json()


def test_sarvam_failure_is_502_with_id(monkeypatch):
    fake_llm(monkeypatch, SarvamError("timed out"))
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": "refund"})
    assert r.status_code == 502 and r.json()["id"] == sid and "unavailable" in r.json()["detail"]


def test_cap_escalation_over_http(monkeypatch):
    monkeypatch.setenv("MAPPA_CLARITY_MAX_CALLS", "1")
    fake_llm(monkeypatch, ASKING)
    sid = _start()
    client.post("/api/clarity", json={"id": sid, "information": "one"})
    r = client.post("/api/clarity", json={"id": sid, "information": "two"})
    assert r.status_code == 200
    assert r.json() == {"id": sid, "status": "escalate", "done": False, "reason": "too many clarity calls"}


def test_critical_escalation_over_http(monkeypatch):
    fake_llm(monkeypatch, dict(ASKING, critical=True, critical_reason="money to a third party"))
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": "send to my cousin"})
    assert r.json()["status"] == "escalate" and r.json()["id"] == sid
    assert r.json()["reason"] == "money to a third party"


def test_done_path_through_the_real_pipeline(monkeypatch):
    import os
    from mappa import jev as jev_module
    if not os.path.exists(jev_module.MODEL):
        pytest.skip("models/jev.pkl is missing (run python -m mappa.train_jev)")
    fake_llm(monkeypatch, DONE)
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": "refund 4000 txn T5512"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid and body["status"] == "done" and body["done"] is True
    assert body["ticket_id"].startswith("TKT-") and body["route"] in ("AI", "HUMAN")
    assert client.get(f"/api/tickets/{body['ticket_id']}").json()["channel"] == "call"
    again = client.post("/api/clarity", json={"id": sid, "information": "more"})
    assert again.json()["ticket_id"] == body["ticket_id"]


def test_manual_ticket_route_still_works():
    import os
    from mappa import jev as jev_module
    if not os.path.exists(jev_module.MODEL):
        pytest.skip("models/jev.pkl is missing (run python -m mappa.train_jev)")
    r = client.post("/api/tickets", json={
        "channel": "manual", "raised_by": PARTY, "category": "device", "subject": "Soundbox not announcing payments",
        "details": {"device_issue": "no sound from speaker", "offline_days": 3}})
    assert r.status_code == 200
    body = r.json()
    assert body["id"].startswith("TKT-") and body["channel"] == "manual"
    assert body["status"] in ("closed", "human_queue") and body["clarity"]["decision"]["route"] in ("AI", "HUMAN")


def test_internal_key_error_is_not_reported_as_unknown_session(monkeypatch):
    from mappa import pipeline

    def broken(body):
        raise KeyError("bug in the ticket path")

    monkeypatch.setattr(pipeline, "raise_ticket", broken)
    fake_llm(monkeypatch, DONE)
    sid = _start()
    r = client.post("/api/clarity", json={"id": sid, "information": "refund 4000"})
    assert r.status_code == 500 and r.json() == {"id": sid, "detail": "internal error"}


def test_422_error_detail_does_not_echo_the_input_secret():
    sid = _start()
    secret_text = "my OTP is 482913 " + "x" * 4000       # over the 4000 limit, so validation fails
    r = client.post("/api/clarity", json={"id": sid, "information": secret_text})
    assert r.status_code == 422 and r.json()["id"] == sid
    assert "482913" not in r.text
    assert all("input" not in e and "ctx" not in e for e in r.json()["detail"])


def test_422_detail_does_not_change_manual_ticket_error_body():
    r = client.post("/api/tickets", json={"channel": "manual"})
    assert r.status_code == 422 and "id" not in r.json() and isinstance(r.json()["detail"], list)
