import re

import pytest

from mappa import sessions
from mappa.schema import ClarityEntry, Party


@pytest.fixture(autouse=True)
def store_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "PATH", tmp_path / "clarity_sessions.json")
    return tmp_path / "clarity_sessions.json"


def test_create_get_save_round_trip(store_path):
    party = Party(role="merchant", name="Amal Medicos", phone="9876500002", merchant_id="M2002")
    s = sessions.create(channel="chatbot", raised_by=party, language="hi")
    assert re.fullmatch(r"CLR-[0-9a-f]{8}", s.id)
    assert s.status == "open" and s.calls == 0 and s.entries == [] and s.clarifications == []

    got = sessions.get(s.id)
    assert got is not None
    assert got.channel == "chatbot" and got.language == "hi"
    assert got.raised_by == party

    got.entries.append(ClarityEntry(text="refund 4000 to the customer"))
    got.calls = 1
    got.status = "done"
    got.conclusion = "Refund approved under P-REF-01"
    got.confidence = 0.92
    got.clarifications = ["Which txn?"]
    got.ticket_id = "TKT-000007"
    sessions.save(got)

    again = sessions.get(s.id)
    assert again.entries[0].text == "refund 4000 to the customer"
    assert again.calls == 1 and again.status == "done"
    assert again.conclusion == "Refund approved under P-REF-01"
    assert again.confidence == 0.92 and again.clarifications == ["Which txn?"]
    assert again.ticket_id == "TKT-000007"
    assert again.updated_at >= again.created_at


def test_unknown_id_returns_none(store_path):
    assert sessions.get("CLR-deadbeef") is None
    sessions.create()
    assert sessions.get("CLR-deadbeef") is None


def test_defaults_and_unique_ids(store_path):
    a = sessions.create()
    b = sessions.create()
    assert a.channel == "call" and a.raised_by is None
    assert a.id != b.id
    assert len(sessions.get(a.id).entries) == 0


def test_save_is_atomic_and_leaves_no_tmp(store_path):
    s = sessions.create()
    s.calls = 2
    sessions.save(s)
    assert store_path.exists()
    assert not store_path.with_suffix(".tmp").exists()


def test_corrupt_store_is_not_overwritten(store_path):
    import json

    store_path.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        sessions.create()
    assert store_path.read_text() == "{not json"


def test_concurrent_creates_keep_every_session(store_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as ex:
        ids = list(ex.map(lambda _: sessions.create().id, range(40)))
    assert len(set(ids)) == 40
    assert all(sessions.get(i) is not None for i in ids)


def test_concurrent_saves_do_not_lose_updates(store_path):
    from concurrent.futures import ThreadPoolExecutor

    ss = [sessions.create() for _ in range(20)]

    def bump(s):
        s.calls += 1
        sessions.save(s)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(bump, ss))
    assert all(sessions.get(s.id).calls == 1 for s in ss)


def test_model_rejects_unknown_channel_and_status(store_path):
    from pydantic import ValidationError

    from mappa.schema import ClaritySession

    base = dict(id="CLR-00000001", created_at=sessions.create().created_at, updated_at=sessions.create().created_at)
    with pytest.raises(ValidationError):
        ClaritySession(**base, channel="email")
    with pytest.raises(ValidationError):
        ClaritySession(**base, status="closed")
