"""Local ticket store: one JSON file (data/tickets.json), rewritten atomically. Swap for a database later."""

import json
import os
import threading
from pathlib import Path

from .schema import Ticket

ROOT = Path(__file__).resolve().parents[1]
PATH = Path(os.environ.get("MAPPA_TICKETS", ROOT / "data" / "tickets.json"))
_lock = threading.RLock()


def _read() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {"next": 1, "tickets": {}}


def _write(db: dict):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1, default=str))
    tmp.replace(PATH)


def next_id() -> str:
    with _lock:
        db = _read()
        n = db["next"]
        db["next"] = n + 1
        _write(db)
        return f"TKT-{n:06d}"


def save(t: Ticket) -> Ticket:
    with _lock:
        db = _read()
        db["tickets"][t.id] = t.model_dump(mode="json")
        _write(db)
    return t


def get(tid: str) -> Ticket | None:
    raw = _read()["tickets"].get(tid)
    return Ticket.model_validate(raw) if raw else None


def all_tickets() -> list[Ticket]:
    return sorted((Ticket.model_validate(r) for r in _read()["tickets"].values()), key=lambda t: t.created_at, reverse=True)
