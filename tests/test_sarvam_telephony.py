import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from mappa.api import app
from mappa import store as mappa_store, schema, pipeline
import sarvam_server


@pytest.fixture
def client():
    return TestClient(app)


def test_lookup_context_creates_new_ticket_for_unknown_caller(client):
    # Unique caller phone number per test run
    test_phone = f"+9188{int(time.time()*1000) % 100000000:08d}"
    resp = client.post("/api/tools/lookup-context", json={"caller_phone": test_phone})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["is_new_ticket"] is True
    assert data["ticket_id"].startswith("TKT-")

    # Verify ticket was actually created in mappa_store
    t = mappa_store.get(data["ticket_id"])
    assert t is not None
    assert t.channel == "call"
    clean_p = test_phone.replace("+91", "")
    assert t.raised_by.phone == clean_p


def test_lookup_context_finds_existing_active_ticket(client):
    unique_phone = f"87{int(time.time()*1000) % 100000000:08d}"
    # Raise a ticket first
    t = pipeline.raise_ticket(schema.TicketIn(
        channel="call",
        raised_by=schema.Party(role="customer", name="Existing Caller", phone=unique_phone),
        subject="Previous unresolved dispute",
        description="Soundbox offline for 3 days",
        transcript=[schema.Turn(speaker="customer", text="Soundbox offline")]
    ))

    resp = client.post("/api/tools/lookup-context", json={"caller_phone": "+91" + unique_phone})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["ticket_id"] == t.id


def test_outbound_call_raises_new_ticket_when_new(client, monkeypatch):
    unique_phone = f"86{int(time.time()*1000) % 100000000:08d}"
    # Mock httpx in trigger_outbound_call
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"attempt_id": "attempt_test_999"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = client.post("/api/calls/outbound", json={
            "user_phone_number": "+91" + unique_phone,
            "customer_name": "New Caller",
            "issue_summary": "Settlement payment query",
            "ticket_id": "NEW"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dispatched"
        assert data["ticket_id"].startswith("TKT-")
        assert data["interaction_id"] == "attempt_test_999"

        # Verify created in store
        t = mappa_store.get(data["ticket_id"])
        assert t is not None
        assert t.channel == "call"
        assert t.raised_by.phone == unique_phone


def test_webhook_receiver_updates_ticket_transcript_and_history(client):
    unique_phone = f"85{int(time.time()*1000) % 100000000:08d}"
    # Create a ticket
    t = pipeline.raise_ticket(schema.TicketIn(
        channel="call",
        raised_by=schema.Party(role="customer", name="Webhook Test Caller", phone=unique_phone),
        subject="Webhook test subject",
        description="Initial voice call description",
        transcript=[schema.Turn(speaker="customer", text="Call started")]
    ))

    fake_details = {
        "transcript": [
            {"role": "agent", "text": "Paytm support mein aapka swaagat hai."},
            {"role": "user", "text": "Mera settlement Rs 5000 ka abhi tak account me nahi aaya hai."}
        ],
        "recording_url": "https://storage.sarvam.ai/recordings/test.wav",
        "forwarded_recording_url": ""
    }

    with patch("sarvam_server.fetch_full_interaction_details", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = fake_details
        payload = {
            "interaction_id": f"interaction_test_{int(time.time()*1000)}",
            "direction": "INBOUND",
            "ticket_id": t.id,
            "user_phone": "+91" + unique_phone,
            "status": "COMPLETED",
            "duration": 45,
            "variables": {"ticket_id": t.id}
        }
        resp = client.post("/api/webhooks/sarvam", json=payload)
        assert resp.status_code == 200

        # Check updated ticket
        updated_t = mappa_store.get(t.id)
        assert updated_t is not None
        assert len(updated_t.transcript) == 2
        assert updated_t.transcript[1].text == "Mera settlement Rs 5000 ka abhi tak account me nahi aaya hai."
        assert any("Sarvam voice call" in h.note for h in updated_t.history if h.note)

