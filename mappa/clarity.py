"""The clarity module: turns a raised ticket into a fixed structure, then asks Jev who should own it.

    intake     sanitise every free-text field (strip OTP/PIN/CVV, redact PII) before anything is stored
    1 text     customer_text = description + the customer's transcript turns (chatbot / call)
    2 details  filed details, plus amount / transaction ID / days / refund kind recovered from the text if missing
    3 category filed > keyword match > Jev's category answer (confidence ≥ 0.8) > "other"
    4 scenario first matching rule of the category (taxonomy.SCENARIOS) -> action item
    5 missing  required details for the category that are still empty
    6 signals  text guard on every customer turn, human-request pattern
    7 policies ALPHA retrieval of the clauses that govern this ticket + human-only clauses whose trigger matched
    7b mcp     capability.check recorded on the ticket (for chat / call it already decided need_human in clarify.py)
    8 decision Jev: hard rules, policy holds, feedback revocations, P(ai) ≥ λ̂  ->  AI or HUMAN
    9 suggest  HUMAN only: what similar, well-resolved past tickets did (case memory), else the playbook
    status     needs_info (details missing) | ai_queue | human_queue

Same ticket in, same structure out.
"""

import re

from . import capability, feedback, jev, memory, policies, taxonomy
from .schema import Clarity, Decision, Details, Event, Signals, Ticket, TicketIn, now
from .textguard import HUMAN_ASK, guard_hits, sanitise

AMOUNT = [re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I),
          re.compile(r"([\d,]{3,}(?:\.\d+)?)\s*(?:₹|rs\b|rupees?|rupaye|rupay|रुपये|रुपए)", re.I)]
TXN = re.compile(r"\b(?:txn|transaction|order|utr|rrn|ref(?:erence)?)\s*(?:id|no\.?|number)?\s*[:#-]?\s*((?=[A-Z0-9]*\d)[A-Z0-9]{5,24})\b", re.I)
DAYS = re.compile(r"(\d{1,3})\s*(?:din|days?|दिन)", re.I)
NEW_REFUND = re.compile(r"(want|need|please|kindly|karna|kar do|karo|initiate|issue|process)\b.{0,20}\brefund|refund\b.{0,15}(karna|kar do|karo|chahiye|initiate|issue)", re.I)
NOT_RECEIVED = re.compile(r"(not|never|nahi|nahin)\b.{0,15}(received|reached|credited|mila|aaya|pahuncha)|still waiting", re.I)


def sanitise_ticket(t: TicketIn) -> tuple[TicketIn, bool, int]:
    """A copy of the ticket with every free-text field sanitised; (ticket, secret removed?, PII count)."""
    secret, pii = False, 0

    def clean(x):
        nonlocal secret, pii
        if not x:
            return x
        out, s, n = sanitise(x)
        secret, pii = secret or s, pii + n
        return out

    t = t.model_copy(deep=True)
    t.subject, t.description, t.ask = clean(t.subject) or "[secret removed]", clean(t.description), clean(t.ask)
    t.caller_claims = [clean(c) for c in t.caller_claims]
    for turn in t.transcript:
        turn.text = clean(turn.text)
    return t, secret, pii


def recover_details(d: Details, text: str) -> tuple[Details, list[str]]:
    d, got = d.model_copy(), []
    if d.amount is None:
        for rx in AMOUNT:
            if m := rx.search(text):
                d.amount = float(m.group(1).replace(",", ""))
                got.append("amount")
                break
    if d.txn_id is None and (m := TXN.search(text)):
        d.txn_id = m.group(1).upper()[:24]
        got.append("txn_id")
    if d.offline_days is None and (m := DAYS.search(text)):
        d.offline_days = float(m.group(1))
        got.append("offline_days")
    if d.refund_kind is None and (NEW_REFUND.search(text) or NOT_RECEIVED.search(text)):
        d.refund_kind = "not received" if NOT_RECEIVED.search(text) else "new request"
        got.append("refund_kind")
    return d, got


def run(t: Ticket, secret_removed: bool = False, pii: int = 0) -> Ticket:
    turns = [x.text for x in t.transcript if x.speaker == "customer" and x.text]
    pieces = [x for x in [t.description] + turns + t.caller_claims if x]
    text = " ".join(pieces)
    full = f"{t.subject}. {text}"

    details, recovered = recover_details(t.details, full)
    category, source = t.category, "filed"
    jev_view = lambda cat: {"category": cat, "issue": t.subject, "ask": t.ask, "details": details.model_dump(),
                            "caller_words": " ".join([t.description] + turns).strip() or None,
                            "caller_claims": t.caller_claims, "human_requested": human}
    human = t.human_requested or bool(HUMAN_ASK.search(full))
    if category is None:
        if kw := taxonomy.keyword_category(full):
            category, source = kw, "keywords"
        else:
            cat = jev.model().ask(jev_view(None))["category"]
            category, source = (cat["answer"], "jev") if cat["confidence"] >= 0.8 else ("other", "default")
    if category != "refund" and "refund_kind" in recovered:  # only meaningful for refunds
        details.refund_kind, recovered = None, [k for k in recovered if k != "refund_kind"]

    scenario, title, action = taxonomy.scenario_for(category, details, full)
    miss = taxonomy.missing(category, details)
    hits = sorted({h for p in [t.subject] + pieces for h in guard_hits(p)})
    cited = policies.tree().search(f"{title}. {full}"[:1500], category)
    holds = policies.triggered(full, category)

    view = jev_view(category)
    mcp = capability.check(action.code, details)  # recorded for support; the call/chat decision was made in clarify.py
    d = jev.decide(view, guard_hits=hits, money_moving=action.money_moving, policy_hits=holds, scenario=scenario,
                   learned=feedback.state())
    t.clarity = Clarity(customer_text=text, category=category, category_source=source, scenario=scenario,
                        scenario_title=title, action=action, details=details, details_recovered=recovered,
                        details_missing=miss, policies=cited, policy_holds=holds, jev_input=view,
                        signals=Signals(guard_hits=hits, secret_removed=secret_removed, pii_redacted=pii, human_requested=human),
                        decision=Decision(**d), mcp=mcp)
    if d["route"] == "HUMAN":
        t.clarity.suggestion = memory.suggest(t)
    t.status = "needs_info" if miss else "ai_queue" if d["route"] == "AI" else "human_queue"
    t.updated_at = now()
    t.history.append(Event(by="system", status=t.status, note=f"clarity: {scenario} -> {d['route']} ({d['why']})"))
    return t
