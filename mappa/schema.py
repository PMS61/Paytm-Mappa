"""The ticket format. Everything that raises a ticket (the manual form, the chatbot, the phone line) sends a `TicketIn`;
the store keeps a `Ticket`; the clarity module fills `Ticket.clarity`. All enums are closed sets.

    TicketIn   what the channel knows when the ticket is raised
    Ticket     TicketIn + id, timestamps, status, history, clarity
    Clarity    deterministic structure derived from the ticket: category, scenario, action item, missing details,
               safety signals, and the Jev decision (AI or HUMAN)
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Channel = Literal["manual", "chatbot", "call"]
Role = Literal["customer", "merchant"]
Category = Literal["settlement", "refund", "device", "kyc", "payment", "other"]
Status = Literal["open", "needs_info", "ai_queue", "human_queue", "in_progress", "resolved", "closed"]
Route = Literal["AI", "HUMAN"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Party(BaseModel):
    role: Role
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^\+?\d{10,13}$")
    merchant_id: str | None = Field(default=None, max_length=24)


class Details(BaseModel):
    """Facts a resolver needs. Which ones are required depends on the category (taxonomy.REQUIRED)."""
    amount: float | None = Field(default=None, ge=0, lt=1e8)          # ₹ of the settlement / transaction / payment
    txn_id: str | None = Field(default=None, max_length=24)
    refund_amount: float | None = Field(default=None, ge=0, lt=1e8)   # ₹ asked to refund, if different from amount
    refund_kind: Literal["new request", "not received"] | None = None
    bank_name: str | None = Field(default=None, max_length=40)
    account_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    account_changed: bool | None = None                               # added / changed a bank account recently
    account_changed_hours_ago: float | None = Field(default=None, ge=0)
    device_issue: str | None = Field(default=None, max_length=120)
    offline_days: float | None = Field(default=None, ge=0)
    kyc_issue: str | None = Field(default=None, max_length=120)
    started: str | None = Field(default=None, max_length=40)          # when the problem started, in the caller's words


class Turn(BaseModel):
    speaker: Literal["customer", "agent"]
    text: str = Field(max_length=4000)
    at: datetime | None = None


class TicketIn(BaseModel):
    channel: Channel
    raised_by: Party
    category: Category | None = None          # picked on the form / filled by the chatbot or call extractor
    subject: str = Field(min_length=3, max_length=240)
    description: str = Field(default="", max_length=4000)
    ask: str | None = Field(default=None, max_length=240)   # what the customer wants done
    details: Details = Field(default_factory=Details)
    transcript: list[Turn] = Field(default_factory=list)    # chatbot / call only
    caller_claims: list[str] = Field(default_factory=list, max_length=10)  # statements to verify, from the extractor
    human_requested: bool = False
    language: str | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _transcript_matches_channel(self):
        if self.channel in ("chatbot", "call") and not self.transcript:
            raise ValueError(f"a {self.channel} ticket needs its transcript")
        if self.channel == "manual" and self.transcript:
            raise ValueError("a manual ticket has no transcript")
        return self


class Action(BaseModel):
    code: str
    summary: str
    steps: list[str]
    money_moving: bool


class Signals(BaseModel):
    guard_hits: list[str] = []      # deterministic text guard: injection / off-topic patterns in the customer's words
    secret_removed: bool = False    # an OTP / PIN / CVV / password was stripped at intake
    pii_redacted: int = 0           # Aadhaar, card, PAN, UPI, phone, account numbers replaced at intake
    human_requested: bool = False


class Decision(BaseModel):
    route: Route
    why: str                        # the first reason, for one-line display
    reasons: list[str]              # every hard rule / threshold that fired (empty -> AI)
    answers: dict                   # Jev's typed answers: {question: {answer, confidence, probabilities}}
    lam: float                      # λ̂: P(ai) needed to route to AI (conformal risk control at level alpha)
    alpha: float
    model: str
    latency_ms: float


class Clarity(BaseModel):
    version: int = 1
    at: datetime = Field(default_factory=now)
    customer_text: str              # everything the customer said: description + their transcript turns (sanitised)
    category: Category
    category_source: Literal["filed", "keywords", "jev", "default"]
    scenario: str
    scenario_title: str
    action: Action
    details: Details                # filed details + any recovered from the text
    details_recovered: list[str]    # keys filled from free text by deterministic patterns
    details_missing: list[str]
    signals: Signals
    policies: list[dict]            # top policy clauses retrieved for this ticket (ALPHA tree), cited to the resolver
    policy_holds: list[dict]        # human-only clauses whose trigger matched
    jev_input: dict                 # the exact state Jev scored, kept so feedback can retrain on it
    decision: Decision
    suggestion: dict | None = None  # HUMAN tickets: what to do, from similar past tickets that were resolved well


Verdict = Literal["correct", "needed_human", "ai_could_resolve"]


class Feedback(BaseModel):
    """Support's verdict on the route, given when the ticket is resolved or closed."""
    verdict: Verdict
    resolution_worked: bool         # did the resolution fix the customer's problem? only these feed suggestions
    note: str | None = Field(default=None, max_length=1000)
    at: datetime = Field(default_factory=now)


class Event(BaseModel):
    at: datetime = Field(default_factory=now)
    by: Literal["customer", "support", "system"]
    status: Status
    note: str | None = None


class Ticket(TicketIn):
    id: str
    created_at: datetime
    updated_at: datetime
    status: Status = "open"
    clarity: Clarity | None = None
    resolution: str | None = None   # what was done: written by support, or the autopilot's summary
    autopilot: dict | None = None   # AI route: {outcome, result, summary, log: [tool calls], playbook, server, ...}
    feedback: Feedback | None = None
    history: list[Event] = []


class TicketUpdate(BaseModel):
    """PATCH body. The customer adds details; support changes status. New details re-run the clarity module."""
    by: Literal["customer", "support"]
    status: Status | None = None
    details: Details | None = None
    resolution: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)
