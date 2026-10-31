"""Learning from support's verdicts. When support closes a ticket they say whether the route was right:

    correct            the route Jev chose was right
    needed_human       it went to AI but needed a human
    ai_could_resolve   it went to a human but AI would have been enough

Two things update after every verdict, in milliseconds, without retraining the network:

  1. λ̂ is recomputed by conformal risk control over the calibration split plus every verdict, each verdict being a
     (P(ai), needed-a-human) pair. "needed_human" verdicts above λ̂ raise it; "ai_could_resolve" verdicts lower it.
  2. Per scenario, a Beta(1 + bad, 1 + good) posterior on the AI error rate. A scenario loses AI routing as soon as
     P(error rate > 10%) > 95%, so one bad AI outcome with no track record revokes it; it earns it back only from
     verdicts on human-handled tickets that AI could have resolved. Revoke fast, restore slowly.

Hard rules never change with feedback. `python -m mappa.train_jev` also folds the verdicts into the training set.
"""

import json
import threading
import time

import numpy as np
from scipy.stats import beta

from .jev import ALPHA, ROOT, model
from .schema import now

PATH = ROOT / "data" / "feedback.jsonl"
STATE = ROOT / "data" / "learning.json"
ERR_TARGET, CONFIDENCE = 0.10, 0.95
_lock = threading.Lock()


def records() -> list[dict]:
    return [json.loads(l) for l in PATH.read_text().splitlines()] if PATH.exists() else []


def state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    M = model()
    return {"lam": M.lam, "lam_trained": M.lam, "revoked": {}, "scenarios": {}, "n_feedback": 0, "history": []}


def true_route(route: str, verdict: str) -> str:
    return route if verdict == "correct" else "HUMAN" if verdict == "needed_human" else "AI"


def add(ticket, verdict: str, note: str | None) -> dict:
    d = ticket.clarity.decision
    rec = {"at": now().isoformat(), "ticket_id": ticket.id, "scenario": ticket.clarity.scenario, "route": d.route,
           "verdict": verdict, "true_route": true_route(d.route, verdict), "p_ai": d.answers["route"]["probabilities"]["ai"],
           "hard_rules": any(r.startswith(("hard rule", "policy")) for r in d.reasons), "note": note,
           "jev_input": ticket.clarity.jev_input}
    with _lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return learn()


def learn() -> dict:
    from .train_jev import crc_threshold
    M, fb = model(), records()
    # 1. λ̂ over calibration + verdicts. Hard-rule tickets are excluded: their route never depended on P(ai).
    use = [r for r in fb if not r["hard_rules"]]
    s = np.concatenate([M.cal_pa, [r["p_ai"] for r in use]])
    l = np.concatenate([M.cal_need, [r["true_route"] == "HUMAN" for r in use]]).astype(float)
    lam = crc_threshold(s, l, ALPHA)
    # 2. per-scenario Beta posterior on the AI error rate
    sc = {}
    for r in fb:
        x = sc.setdefault(r["scenario"], {"good": 0, "bad": 0})
        if r["route"] == "AI":
            x["good" if r["verdict"] == "correct" else "bad"] += 1
        elif r["verdict"] == "ai_could_resolve":
            x["good"] += 1
    revoked = {}
    for k, x in sc.items():
        x["p_err_above_target"] = round(float(beta.sf(ERR_TARGET, 1 + x["bad"], 1 + x["good"])), 4)
        if x["bad"] and x["p_err_above_target"] > CONFIDENCE:
            revoked[k] = f"{x['bad']} AI outcome(s) needed a human, {x['good']} good: P(error > {ERR_TARGET:.0%}) = {x['p_err_above_target']:.2f}"
    old = state()
    st = {"lam": lam, "lam_trained": M.lam, "revoked": revoked, "scenarios": sc, "n_feedback": len(fb),
          "history": (old.get("history") or [])[-199:] + [{"at": now().isoformat(), "n": len(fb), "lam": round(lam, 4),
                                                           "revoked": sorted(revoked)}],
          "updated": time.time()}
    STATE.write_text(json.dumps(st, indent=1))
    return st
