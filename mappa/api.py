"""HTTP API + the two pages.   uvicorn mappa.api:app --port 8000

    /                    customer / merchant portal          /support    Paytm support desk (queue, ticket, log)
    GET   /api/taxonomy                  categories, required details, scenarios, action items
    POST  /api/tickets                   raise a ticket (TicketIn) -> sanitised, clarity + Jev run, stored
    GET   /api/tickets?status=&route=&phone=
    GET   /api/tickets/{id}
    PATCH /api/tickets/{id}              TicketUpdate: customer adds details (clarity re-runs) / support sets status
    POST  /api/tickets/{id}/feedback     Feedback on a resolved / closed ticket -> Jev recalibrates
    (AI-routed tickets run the autopilot over the Paytm actions MCP server on creation; see autopilot.py)
    GET   /api/policies?q=&category=     policy search (ALPHA tree)
    GET   /api/log                       decisions, verdicts and the learning state
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import autopilot, clarity, feedback, memory, policies, store, taxonomy
from .schema import Event, Feedback, Ticket, TicketIn, TicketUpdate, now
from .textguard import sanitise

app = FastAPI(title="Paytm support: tickets, clarity, Jev")
WEB = store.ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def customer_page():
    return FileResponse(WEB / "customer.html")


@app.get("/support")
def support_page():
    return FileResponse(WEB / "support.html")


@app.get("/api/taxonomy")
def get_taxonomy():
    return taxonomy.describe()


@app.post("/api/tickets", response_model=Ticket)
def raise_ticket(body: TicketIn):
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


@app.get("/api/tickets", response_model=list[Ticket])
def list_tickets(status: str | None = None, route: str | None = None, phone: str | None = None, review: bool = False):
    """review=true: closed or resolved tickets still waiting for support's verdict."""
    return [t for t in store.all_tickets()
            if (not status or t.status == status) and (not review or (t.status in ("resolved", "closed") and not t.feedback)) and (not phone or t.raised_by.phone == phone)
            and (not route or (t.clarity and t.clarity.decision.route == route))]


def _get(tid: str) -> Ticket:
    if not (t := store.get(tid)):
        raise HTTPException(404, f"no ticket {tid}")
    return t


@app.get("/api/tickets/{tid}", response_model=Ticket)
def get_ticket(tid: str):
    return _get(tid)


@app.patch("/api/tickets/{tid}", response_model=Ticket)
def update_ticket(tid: str, body: TicketUpdate):
    t = _get(tid)
    if body.status and body.by == "customer":
        raise HTTPException(403, "only support changes the status")
    if body.status in ("resolved", "closed") and not (body.resolution or t.resolution):
        raise HTTPException(422, "write what was done (resolution) before resolving or closing")
    if body.resolution:
        t.resolution = sanitise(body.resolution)[0]
    if body.details:
        merged = t.details.model_dump() | body.details.model_dump(exclude_none=True)
        t.details = type(t.details)(**merged)
        sig = t.clarity.signals if t.clarity else None
        t = process(clarity.run(t, sig.secret_removed if sig else False, sig.pii_redacted if sig else 0))
    if body.status:
        t.status = body.status
        t.history.append(Event(by=body.by, status=body.status, note=body.note))
    elif body.note:
        t.history.append(Event(by=body.by, status=t.status, note=body.note))
    t.updated_at = now()
    return store.save(t)


@app.post("/api/tickets/{tid}/feedback")
def give_feedback(tid: str, body: Feedback):
    t = _get(tid)
    if t.status not in ("resolved", "closed"):
        raise HTTPException(409, "feedback is given once the ticket is resolved or closed")
    if t.feedback:
        raise HTTPException(409, "feedback already given for this ticket")
    t.feedback = body
    t.history.append(Event(by="support", status=t.status, note=f"feedback: {body.verdict}"))
    store.save(t)
    st = feedback.add(t, body.verdict, body.note)
    return {"ticket": t, "learning": st}


@app.get("/api/policies")
def search_policies(q: str, category: str | None = None, k: int = 5):
    return policies.tree().search(q, category, k)


@app.get("/api/log")
def log():
    decisions = [{"ticket_id": t.id, "at": t.clarity.at, "channel": t.channel, "scenario": t.clarity.scenario,
                  "route": t.clarity.decision.route, "why": t.clarity.decision.why,
                  "p_ai": t.clarity.decision.answers["route"]["probabilities"]["ai"], "status": t.status,
                  "verdict": t.feedback.verdict if t.feedback else None,
                  "autopilot": t.autopilot["outcome"] if t.autopilot else None}
                 for t in store.all_tickets() if t.clarity]
    return {"decisions": decisions, "feedback": [{k: v for k, v in r.items() if k != "jev_input"} for r in feedback.records()[::-1]], "learning": feedback.state()}
