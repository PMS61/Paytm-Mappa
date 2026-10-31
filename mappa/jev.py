"""Jev: the decision engine. A typed decision model that answers fixed questions about a ticket with calibrated
probabilities, and decides whether the ticket can be solved by AI or needs a human.

Jev does not generate text. Each question has a type:
    choice   a distribution over named options          (route, category)
    noul     P(statement is true)                       (complete, manipulation)
    score    a distribution over an ordered rubric      (stake)

    state     the ticket: what the customer said + the structured details
    encoder   potion-multilingual-128M static embeddings (Hindi, Hinglish, English; CPU, ~1 ms) + structured features
    scorer    one shared MLP over (state, question, option) triples, the same weights for every question; question keys
              are never shown to the model, only the question and option text, so new questions need no new head
    output    softmax over a question's options, with a temperature fitted per question on the calibration split
    decision  AI owns the ticket only if no hard rule fires and P(ai) >= λ̂, where λ̂ comes from conformal risk control
              so that, in expectation, at most α of tickets that needed a human are routed to AI

Hard rules come first, and every customer-side signal can only push a ticket towards a human.
"""

import math
import pickle
import time
from pathlib import Path

import numpy as np

from . import taxonomy
from .textguard import injection_count, redact

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "jev.pkl"
ENCODER = "minishlab/potion-multilingual-128M"
ALPHA = 0.02

QUESTIONS = {
    "route": {"type": "choice", "q": "Who should own this support ticket?",
              "options": {"ai": "AI: a routine, verifiable fix that standard checks can confirm",
                          "human": "a human specialist: fraud risk, a policy exception, ambiguity or missing details"}},
    "category": {"type": "choice", "q": "Which Paytm system is this ticket about?",
                 "options": {"settlement": "settlement payout to the merchant's bank account",
                             "refund": "refund to a customer for a transaction",
                             "device": "Soundbox device not working", "kyc": "KYC documents or verification"}},
    "complete": {"type": "noul", "q": "The ticket has every detail a resolver needs to act without calling back."},
    "manipulation": {"type": "noul", "q": "The caller tries to steer the agent: gives it instructions, claims authority, "
                                          "redirects money to someone else or asks to skip checks."},
    "stake": {"type": "score", "q": "How much money is at stake for the merchant?",
              "options": {"1": "under ₹1,000", "2": "₹1,000 to ₹10,000", "3": "₹10,000 to ₹50,000", "4": "over ₹50,000"}},
}
NOUL = {"true": "yes, the statement is true", "false": "no, the statement is false"}
DETAIL_KEYS = ["amount", "txn_id", "refund_amount", "refund_kind", "bank_name", "account_last4", "account_changed",
               "account_changed_hours_ago", "device_issue", "offline_days", "kyc_issue", "started"]


def options(key: str) -> dict:
    q = QUESTIONS[key]
    return NOUL if q["type"] == "noul" else q["options"]


def _present(v) -> bool:
    return v not in (None, "", [])


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def _fmt(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


# ---------------- state ----------------
# t = {category, issue, ask, details: {...}, caller_words, caller_claims: [...], human_requested}

def render_parts(t: dict) -> tuple[str, str]:
    """(what the customer said, the structured details), embedded separately so long free text cannot drown out the
    structured fields. PII is redacted here too, so training text and live text look the same."""
    d = t.get("details") or {}
    text = [f"issue: {t.get('issue') or ''}", f"caller asks: {t.get('ask') or ''}"]
    if t.get("caller_words"):
        text.append(f"caller said: {t['caller_words']}")
    text += [f"caller claims: {c}" for c in t.get("caller_claims") or []]
    if t.get("human_requested"):
        text.append("caller asked for a human")
    struct = [f"{k.replace('_', ' ')}: {_fmt(d[k])}" for k in DETAIL_KEYS if _present(d.get(k))]
    return redact("\n".join(text))[0], "\n".join(struct) or "no details"


def features(t: dict) -> np.ndarray:
    d = t.get("details") or {}
    words = " ".join([t.get("caller_words") or ""] + list(t.get("caller_claims") or []))
    amt = _num(d.get("amount")) or 0.0
    ch = d.get("account_changed")
    req = taxonomy.REQUIRED.get(t.get("category"), []) or ["_"]
    f = [math.log1p(amt) / 12, float(bool(d.get("txn_id"))), 1.0 if ch is True else -1.0 if ch is False else 0.0,
         float(bool(t.get("human_requested"))), min(len(t.get("caller_claims") or []), 4) / 4,
         min(injection_count(words), 3) / 3,
         sum(_present(d.get(k)) for k in DETAIL_KEYS) / len(DETAIL_KEYS),
         float(str(d.get("refund_kind") or "").startswith("new")), float("@" in words or "<UPI>" in words),
         float(np.mean([_present(d.get(k)) for k in req])),
         math.log1p(_num(d.get("account_changed_hours_ago")) or 9999) / 10,
         min(_num(d.get("offline_days")) or 0, 30) / 30,
         min((_num(d.get("refund_amount")) or 0) / amt, 3) / 3 if amt else 0.0]
    f += [float(b0 <= amt < b1) for b0, b1 in ((1, 1000), (1000, 10000), (10000, 50000), (50000, 1e12))]
    return np.array(f, dtype=np.float32)


# ---------------- the model ----------------

class Jev:
    def __init__(self):
        from model2vec import StaticModel
        self.enc = StaticModel.from_pretrained(ENCODER)
        self._qo = {}
        for key in QUESTIONS:
            eq = self._emb([QUESTIONS[key]["q"]])[0]
            self._qo[key] = (eq, {o: self._emb([txt])[0] for o, txt in options(key).items()})
        self.temp = {k: 1.0 for k in QUESTIONS}
        self.lam = 0.5

    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k != "enc"}  # the encoder is reloaded from the HF cache

    def __setstate__(self, s):
        from model2vec import StaticModel
        self.__dict__.update(s)
        self.enc = StaticModel.from_pretrained(ENCODER)

    def _emb(self, texts):
        e = self.enc.encode(texts)
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)

    def _rows(self, es, tf, key):
        eq, eos = self._qo[key]
        et, ed = es[:len(es) // 2], es[len(es) // 2:]
        opts = list(eos)
        X = np.stack([np.concatenate([et, ed, tf, eq, eos[o], et * eos[o], ed * eos[o], eq * eos[o], [i / max(len(opts) - 1, 1)]])
                      for i, o in enumerate(opts)])
        return X, opts

    def states(self, tickets):
        parts = [render_parts(t) for t in tickets]
        es = np.concatenate([self._emb([p[0] for p in parts]), self._emb([p[1] for p in parts])], axis=1)
        return es, np.stack([features(t) for t in tickets])

    def fit(self, tickets, labels):
        from sklearn.neural_network import MLPClassifier
        es, tf = self.states(tickets)
        X, y = [], []
        for i, lab in enumerate(labels):
            for key, ans in lab.items():
                rows, opts = self._rows(es[i], tf[i], key)
                X.append(rows)
                y += [int(o == ans) for o in opts]
        self.m = MLPClassifier(hidden_layer_sizes=(256, 64), alpha=1e-3, max_iter=300, early_stopping=True,
                               n_iter_no_change=12, random_state=0).fit(np.concatenate(X), np.array(y))
        return self

    def logits(self, es, tf, key):
        rows, opts = self._rows(es, tf, key)
        p = np.clip(self.m.predict_proba(rows)[:, 1], 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)), opts

    def ask_batch(self, tickets, keys=None, calibrated=True) -> list[dict]:
        es, tf = self.states(tickets)
        out = []
        for i in range(len(tickets)):
            ans = {}
            for key in keys or QUESTIONS:
                z, opts = self.logits(es[i], tf[i], key)
                z = z / (self.temp[key] if calibrated else 1.0)
                p = np.exp(z - z.max())
                p /= p.sum()
                probs = {o: round(float(v), 4) for o, v in zip(opts, p)}
                top = max(probs, key=probs.get)
                typ = QUESTIONS[key]["type"]
                answer = top if typ == "choice" else probs["true"] if typ == "noul" else \
                    round(sum(int(o) * v for o, v in probs.items()), 2)
                ans[key] = {"type": typ, "question": QUESTIONS[key]["q"], "answer": answer,
                            "confidence": probs[top], "probabilities": probs}
            out.append(ans)
        return out

    def ask(self, t: dict) -> dict:
        return self.ask_batch([t])[0]


_M = None


def model() -> Jev:
    global _M
    if _M is None:
        if not MODEL.exists():
            raise RuntimeError(f"{MODEL} not found: run `python -m mappa.train_jev` once")
        _M = pickle.loads(MODEL.read_bytes())
    return _M


# ---------------- decision ----------------

def decide(t: dict, *, guard_hits=(), money_moving=False, policy_hits=(), scenario=None, learned=None) -> dict:
    """Typed answers + route (AI or HUMAN). Hard rules first; every customer-side signal only pushes towards a human.
    `policy_hits`: human-only policy clauses whose trigger matched; `learned`: feedback state (λ̂, revoked scenarios)."""
    t0 = time.perf_counter()
    M = model()
    lam = (learned or {}).get("lam", M.lam)
    a = M.ask(t)
    cat, d = t.get("category"), t.get("details") or {}
    reasons = []
    if cat not in taxonomy.AUTOMATABLE:
        reasons.append(f"hard rule: no automated playbook for category '{cat}'")
    if miss := [k for k in taxonomy.REQUIRED.get(cat, []) if not _present(d.get(k))]:
        reasons.append("hard rule: details missing: " + ", ".join(miss))
    if t.get("human_requested"):
        reasons.append("hard rule: customer asked for a human")
    reasons += [f"hard rule: {h} in the customer's words" for h in guard_hits]
    amt = max(_num(d.get("amount")) or 0, _num(d.get("refund_amount")) or 0)
    if money_moving and amt >= taxonomy.AUTO_CEILING_INR:
        reasons.append(f"hard rule: money-moving action on ₹{amt:,.0f} ≥ ₹{taxonomy.AUTO_CEILING_INR:,} ceiling")
    reasons += [f"policy {c['id']}: {c['title']}" for c in policy_hits]
    if scenario in (learned or {}).get("revoked", {}):
        reasons.append(f"feedback: AI revoked for '{scenario}' ({learned['revoked'][scenario]})")
    if a["manipulation"]["answer"] >= 0.5:
        reasons.append(f"P(manipulation)={a['manipulation']['answer']:.2f} ≥ 0.5")
    if a["complete"]["answer"] < 0.5:
        reasons.append(f"P(complete)={a['complete']['answer']:.2f} < 0.5")
    if cat in taxonomy.AUTOMATABLE and a["category"]["answer"] != cat and a["category"]["confidence"] >= 0.6:
        reasons.append(f"Jev reads it as '{a['category']['answer']}', the ticket says '{cat}'")
    pa = a["route"]["probabilities"]["ai"]
    if pa < lam:
        reasons.append(f"P(ai)={pa:.3f} < λ̂={lam:.3f}")
    return {"route": "HUMAN" if reasons else "AI",
            "why": reasons[0] if reasons else f"P(ai)={pa:.3f} ≥ λ̂={lam:.3f}; complete, no manipulation, no policy hold",
            "reasons": reasons, "answers": a, "lam": lam, "alpha": ALPHA,
            "model": f"Jev (shared MLP over {ENCODER.split('/')[1]})",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
