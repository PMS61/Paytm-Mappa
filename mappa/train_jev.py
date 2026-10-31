"""Train and benchmark Jev.   python -m mappa.train_jev   ->  models/jev.pkl, results/jev.json

Data: data/jev_tickets.jsonl, 3,186 labelled tickets built from our simulated merchant-support benchmark (750 cases):
the clean ticket plus variants with a required detail dropped, callers who ask for a human, and callers whose words
come from a goal-disjoint attack suite. Splits:
    train  fit the scorer            cal   temperatures + λ̂ only
    test   in-distribution           ood   case types never seen in training      adv   social-engineering variants
"""

import json
import time

import numpy as np

from . import feedback, jev, taxonomy
from .feedback import records as feedback_records
from .jev import ALPHA, QUESTIONS, ROOT, Jev
from .schema import Details
from .policies import triggered
from .textguard import guard_hits

DATA = ROOT / "data" / "jev_tickets.jsonl"
RESULTS = ROOT / "results" / "jev.json"


def crc_threshold(scores, losses, alpha: float) -> float:
    """Conformal risk control: the smallest λ with n/(n+1)·R̂(λ) + 1/(n+1) <= α, which gives E[loss] <= α on a new
    exchangeable ticket. Here loss = 1[ticket needed a human and P(ai) >= λ]."""
    s, l = np.asarray(scores, float), np.asarray(losses, float)
    n = len(s)
    for lam in np.unique(np.concatenate([s, [np.inf]])):
        risk = np.mean(l * (s >= lam)) if np.isfinite(lam) else 0.0
        if n / (n + 1) * risk + 1 / (n + 1) <= alpha:
            return float(lam)
    return float("inf")


def ece(conf, correct, bins=10) -> float:
    conf, correct = np.asarray(conf), np.asarray(correct, float)
    e, edges = 0.0, np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(conf[m].mean() - correct[m].mean())
    return float(e)


def fit_temperatures(M: Jev, tickets, labels):
    es, tf = M.states(tickets)
    for key in QUESTIONS:
        Z, Y = [], []
        for i, lab in enumerate(labels):
            z, opts = M.logits(es[i], tf[i], key)
            Z.append(z)
            Y.append(opts.index(lab[key]))
        best = (1e9, 1.0)
        for T in np.exp(np.linspace(np.log(0.2), np.log(8), 60)):
            nll = sum(-(z / T)[y] + (z / T).max() + np.log(np.exp(z / T - (z / T).max()).sum()) for z, y in zip(Z, Y))
            best = min(best, (nll, float(T)))
        M.temp[key] = best[1]


def _hard_rules(t) -> bool:
    words = [t.get("caller_words") or ""] + list(t.get("caller_claims") or [])
    return bool(taxonomy.missing(t["category"], Details(**t.get("details") or {})) or t.get("human_requested")
                or any(guard_hits(w) for w in words if w) or triggered(" ".join(words), t["category"]))


def evaluate(M: Jev, tickets, labels, calibrated=True) -> dict:
    A = M.ask_batch(tickets, calibrated=calibrated)
    r = {}
    for key in QUESTIONS:
        pred = [max(a[key]["probabilities"], key=a[key]["probabilities"].get) for a in A]
        ok = [p == l[key] for p, l in zip(pred, labels)]
        r[key] = {"acc": round(float(np.mean(ok)), 4), "ece": round(ece([a[key]["confidence"] for a in A], ok), 4)}
    need = np.array([l["route"] == "human" for l in labels])
    pa = np.array([a["route"]["probabilities"]["ai"] for a in A])
    hard = np.array([_hard_rules(t) for t in tickets])

    def rates(to_ai):
        return {"missed_human": round(float(np.mean(to_ai & need)), 4),               # needed a human, routed to AI
                "ai_coverage": round(float(np.mean(to_ai & ~need) / max((~need).mean(), 1e-9)), 4)}  # routine tickets AI kept
    r["routing"] = {"n": len(labels), "human_share": round(float(need.mean()), 4),
                    "jev_only": rates(pa >= M.lam),
                    "rules_only": rates(~hard),                       # baseline: hard rules + policy triggers, no model
                    "deployed": rates((pa >= M.lam) & ~hard),                       # what the clarity module runs
                    "manip_recall": round(float(np.mean([a["manipulation"]["answer"] >= 0.5 for a, l in zip(A, labels)
                                                         if l["manipulation"] == "true"] or [np.nan])), 4)}
    return r


def main():
    rows = [json.loads(l) for l in DATA.read_text().splitlines()]
    split = {sp: ([r["ticket"] for r in rows if r["split"] == sp], [r["labels"] for r in rows if r["split"] == sp])
             for sp in ("train", "cal", "test", "ood", "adv")}
    fb = [r for r in feedback_records() if r.get("jev_input")]  # support's verdicts become training tickets
    split["train"][0].extend(r["jev_input"] for r in fb)
    split["train"][1].extend({"route": "ai" if r["true_route"] == "AI" else "human"} for r in fb)
    t0 = time.time()
    M = Jev().fit(*split["train"])
    print(f"fit {len(split['train'][0])} tickets in {time.time() - t0:.0f}s")
    fit_temperatures(M, *split["cal"])
    cal_t, cal_l = split["cal"]
    pa = np.array([a["route"]["probabilities"]["ai"] for a in M.ask_batch(cal_t, keys=["route"])])
    M.cal_pa, M.cal_need = pa, np.array([l["route"] == "human" for l in cal_l])  # kept for feedback recalibration
    M.lam = crc_threshold(pa, M.cal_need.astype(float), ALPHA)
    res = {"feedback_tickets": len(fb), "alpha": ALPHA, "lambda": round(M.lam, 4), "temperatures": {k: round(v, 3) for k, v in M.temp.items()},
           "encoder": jev.ENCODER, "n": {k: len(v[0]) for k, v in split.items()}}
    for sp in ("test", "ood", "adv"):
        res[sp] = evaluate(M, *split[sp])
        res[sp + "_uncalibrated_ece"] = {k: v["ece"] for k, v in evaluate(M, *split[sp], calibrated=False).items()
                                         if k in QUESTIONS}
        print(sp, json.dumps(res[sp]))
    t1 = time.perf_counter()
    for t in split["test"][0][:50]:
        M.ask(t)
    res["latency_ms_per_ticket"] = round((time.perf_counter() - t1) * 1000 / 50, 2)
    jev.MODEL.parent.mkdir(exist_ok=True)
    jev.MODEL.write_bytes(__import__("pickle").dumps(M))
    RESULTS.write_text(json.dumps(res, indent=1))
    feedback.STATE.unlink(missing_ok=True)  # the new model's λ̂ supersedes the old learning state
    print("λ̂", res["lambda"], "latency ms", res["latency_ms_per_ticket"])


if __name__ == "__main__":
    from mappa.train_jev import main as _main  # so the pickle refers to mappa.jev.Jev, not __main__
    _main()
