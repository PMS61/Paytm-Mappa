"""The ticket path shared by every channel: sanitise -> Ticket -> clarity + Jev -> autopilot / human queue -> store.

    raise_ticket(body)   what POST /api/tickets runs, and what a finished clarity session calls
    process(t)          AI-routed tickets go to the autopilot; closed when resolved, else to a human queue
"""

from . import autopilot, clarity, memory, store
from .schema import Event, Ticket, TicketIn, now


def raise_ticket(body: TicketIn) -> Ticket:
    clean, secret, pii = clarity.sanitise_ticket(body)
    t = Ticket(**clean.model_dump(), id=store.next_id(), created_at=now(), updated_at=now(),
               history=[Event(by="customer", status="open", note=f"raised via {body.channel}")])
    return store.save(process(clarity.run(t, secret, pii)))


def process(t: Ticket) -> Ticket:
    """AI-routed tickets go straight to the autopilot: closed when resolved (awaiting review), else to a human."""
    if t.status != "ai_queue":
        return t
    a = t.autopilot = autopilot.run(t)
    if a["outcome"] == "resolved":
        t.status, t.resolution = "closed", a["summary"]
        t.history.append(Event(by="system", status="closed", note="autopilot resolved it; awaiting support review"))
    else:
        t.status = "human_queue"
        t.clarity.suggestion = memory.suggest(t)
        t.history.append(Event(by="system", status="human_queue", note=f"autopilot handoff: {a['result']}"))
    return t
