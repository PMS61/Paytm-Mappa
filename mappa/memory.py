"""Case memory: suggestions for HUMAN tickets from how similar past tickets were resolved.

Only tickets that support closed with a resolution *and* feedback saying the resolution worked are remembered. The new
ticket is embedded with Jev's encoder; the k nearest remembered tickets (cosine ≥ MIN_SIM) are returned with their
resolutions, ranked by similarity with a bonus for the same scenario. With no such history yet, the suggestion falls
back to the scenario's playbook and the cited policy clauses. Deterministic: an LLM can rephrase it, not invent it.
"""

import numpy as np

from . import store

K, MIN_SIM, SAME_SCENARIO_BONUS = 3, 0.55, 0.1
_cache: dict[str, np.ndarray] = {}


def _text(t) -> str:
    c = t.clarity
    return f"{c.scenario_title}. {t.subject}. {c.customer_text}"[:2000]


def _emb(texts):
    from .jev import model
    e = model().enc.encode(texts)
    return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)


def remembered():
    return [t for t in store.all_tickets() if t.status in ("resolved", "closed") and t.resolution and t.clarity
            and t.feedback and t.feedback.resolution_worked]


def suggest(ticket) -> dict:
    c = ticket.clarity
    past = [t for t in remembered() if t.id != ticket.id]
    similar = []
    if past:
        new = [t for t in past if t.id not in _cache]
        if new:
            for t, v in zip(new, _emb([_text(t) for t in new])):
                _cache[t.id] = v
        q = _emb([_text(ticket)])[0]
        scored = [(float(_cache[t.id] @ q) + SAME_SCENARIO_BONUS * (t.clarity.scenario == c.scenario), float(_cache[t.id] @ q), t)
                  for t in past]
        similar = [{"ticket_id": t.id, "similarity": round(sim, 3), "scenario": t.clarity.scenario,
                    "route_was": t.clarity.decision.route, "resolution": t.resolution}
                   for rank, sim, t in sorted(scored, key=lambda x: -x[0])[:K] if sim >= MIN_SIM]
    return {"basis": "similar resolved tickets" if similar else "playbook (no similar resolved ticket yet)",
            "do": [s["resolution"] for s in similar] or c.action.steps,
            "similar": similar, "action": c.action.code,
            "policies": [p["id"] for p in c.policies]}
