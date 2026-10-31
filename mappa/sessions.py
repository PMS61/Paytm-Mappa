"""Clarity session store: one JSON file (data/clarity_sessions.json), rewritten atomically, like store.py.

    create(channel, raised_by, language) -> ClaritySession      new id CLR-xxxxxxxx
    get(id) -> ClaritySession | None
    save(s) -> ClaritySession                                    stamps updated_at, writes the whole session
"""

import contextlib
import fcntl
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

from .schema import ClaritySession, Party, now

ROOT = Path(__file__).resolve().parents[1]
PATH = Path(os.environ.get("MAPPA_SESSIONS", ROOT / "data" / "clarity_sessions.json"))
_lock = threading.RLock()  # threads in this process; the flock below covers other processes


@contextlib.contextmanager
def _locked():
    """Exclusive lock over one read-modify-write, across threads and processes (sidecar file <path>.lock)."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(f"{PATH}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {"sessions": {}}


def _write(db: dict):
    """Write to a unique temp file in the same directory, then rename over the store (atomic)."""
    fd, tmp = tempfile.mkstemp(dir=PATH.parent, prefix=PATH.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(db, ensure_ascii=False, indent=1, default=str))
        os.replace(tmp, PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def create(channel: str = "call", raised_by: Party | None = None, language: str | None = None) -> ClaritySession:
    with _locked():
        db = _read()
        sid = f"CLR-{secrets.token_hex(4)}"
        while sid in db["sessions"]:
            sid = f"CLR-{secrets.token_hex(4)}"
        t = now()
        s = ClaritySession(id=sid, created_at=t, updated_at=t, channel=channel, raised_by=raised_by, language=language)
        db["sessions"][sid] = s.model_dump(mode="json")
        _write(db)
    return s


def get(sid: str) -> ClaritySession | None:
    with _locked():
        raw = _read()["sessions"].get(sid)
    return ClaritySession.model_validate(raw) if raw else None


def save(s: ClaritySession) -> ClaritySession:
    with _locked():
        s.updated_at = now()
        db = _read()
        db["sessions"][s.id] = s.model_dump(mode="json")
        _write(db)
    return s
