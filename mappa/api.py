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

import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import clarify, feedback, pipeline, policies, sessions, store, taxonomy
from .sarvam import SarvamError
from .schema import Event, Feedback, Party, Ticket, TicketIn, TicketUpdate, now
from .textguard import sanitise

app = FastAPI(title="Paytm support: tickets, clarity, Jev")
WEB = store.ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")

# Mount Sarvam Voice & Telephony router
try:
    import sys
    from pathlib import Path
    _root = str(Path(__file__).resolve().parents[1])
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import sarvam_server
    app.include_router(sarvam_server.router)
except Exception as e:
    logging.getLogger(__name__).warning("Could not include sarvam_server router: %s", e)



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
    return pipeline.raise_ticket(body)


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
        t = pipeline.process(clarity.run(t, sig.secret_removed if sig else False, sig.pii_redacted if sig else 0))
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


class ClarityStart(BaseModel):
    """Either a nested `raised_by`, or flat `phone` / `name` / `role` (voice platforms such as Sarvam can only send
    flat fields). `id` lets the caller choose the session id, e.g. Sarvam's Interaction ID; repeating it is idempotent."""
    channel: Literal["chatbot", "call"] = "call"
    raised_by: Party | None = None
    phone: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=80)
    role: Literal["customer", "merchant"] = "merchant"
    id: str | None = Field(default=None, min_length=1, max_length=128)
    language: str | None = Field(default=None, max_length=8)

    def party(self) -> Party | None:
        if self.raised_by:
            return self.raised_by
        phone = (self.phone or "").replace(" ", "").replace("-", "")
        try:  # an unusable phone (e.g. a browser test call) still opens the session; the ticket step escalates later
            return Party(role=self.role, name=(self.name or "Phone caller").strip() or "Phone caller", phone=phone)
        except ValueError:
            return None


class ClarityIn(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    information: str = Field(min_length=1, max_length=4000)


def _clarity_error(status: int, sid: str | None, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"id": sid, "detail": detail, "status": "error",
                                                     "agent_message": clarify.agent_message({"status": "error"})})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Clarity routes answer every error with {"id", "detail"}; other routes keep FastAPI's default body."""
    if not request.url.path.startswith("/api/clarity"):
        return await request_validation_exception_handler(request, exc)
    sid = exc.body.get("id") if isinstance(exc.body, dict) else None
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]  # input may hold a secret
    return JSONResponse(status_code=422, content={"id": sid if isinstance(sid, str) else None,
                                                  "detail": jsonable_encoder(errors), "status": "error",
                                                  "agent_message": clarify.agent_message({"status": "error"})})



@app.post("/api/clarity/sessions")
def start_clarity(body: ClarityStart):
    return {"id": sessions.create(channel=body.channel, raised_by=body.party(), language=body.language, sid=body.id).id}


@app.post("/api/clarity")
def clarity_turn(body: ClarityIn):
    """Every reply carries `agent_message`, one plain instruction for agents that pass a single field to their model.
    An unknown id opens the session on the spot (e.g. Sarvam's on-start hook failed), so the call can still go on."""
    try:
        try:
            r = clarify.step(body.id, body.information)
        except clarify.UnknownSession:
            sessions.create(channel="call", sid=body.id)
            r = clarify.step(body.id, body.information)
        return {**r, "agent_message": clarify.agent_message(r)}
    except clarify.UnknownSession:
        return _clarity_error(404, body.id, f"no clarity session {body.id}")
    except SarvamError as e:
        return _clarity_error(502, body.id, f"clarity LLM unavailable: {e}")
    except Exception:
        logging.getLogger(__name__).exception("clarity turn failed for %s", body.id)
        return _clarity_error(500, body.id, "internal error")


@app.get("/api/clarity/{sid:path}")
def get_clarity(sid: str):
    s = sessions.get(sid)
    return s if s else _clarity_error(404, sid, f"no clarity session {sid}")


@app.get("/api/log")
def log():
    decisions = [{"ticket_id": t.id, "at": t.clarity.at, "channel": t.channel, "scenario": t.clarity.scenario,
                  "route": t.clarity.decision.route, "why": t.clarity.decision.why,
                  "p_ai": t.clarity.decision.answers["route"]["probabilities"]["ai"], "status": t.status,
                  "verdict": t.feedback.verdict if t.feedback else None,
                  "autopilot": t.autopilot["outcome"] if t.autopilot else None}
                 for t in store.all_tickets() if t.clarity]
    return {"decisions": decisions, "feedback": [{k: v for k, v in r.items() if k != "jev_input"} for r in feedback.records()[::-1]], "learning": feedback.state()}
