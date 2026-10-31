"""Policy RAG: ALPHA's retrieval tree over the policy documents in policies/*.md.

    tree      document (file) -> page (## topic) -> unit (### clause)
    filter    ALPHA's hard document filter: only documents whose `applies:` covers the ticket's category, plus `all`
    score     unit = z(dense) + 0.5·z(BM25); a page scores as its best unit + 0.5·z(page BM25), and borrows 0.3 from
              its neighbours in the same document (relevant clauses cluster); a clause ranks by page + unit score
    triggers  a clause marked `route: human` reserves the ticket for a human when its `trigger:` pattern matches the
              customer's words. This is deliberately a pattern, not a similarity: similarity says what a clause is
              about, not whether its condition holds, and every refund ticket is *about* refunds.

ALPHA's LLM stages (per-unit verification and the coherence refinement over the verdict pattern) plug in after
`search` once an LLM is attached; the decision engine only needs the deterministic part.
"""

import re
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "policies"


def _tok(texts):
    return [re.findall(r"[a-z0-9][a-z0-9.%]*", t.lower()) for t in texts]


def _z(x):
    return (x - x.mean()) / (x.std() + 1e-6) if len(x) > 1 else np.zeros_like(x)


def parse(path: Path) -> list[dict]:
    doc, applies, page, units, cur = path.stem, ["all"], "", [], None
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("applies:"):
            applies = [a.strip() for a in line.split(":", 1)[1].split(",")]
        elif line.startswith("## "):
            page = line[3:].strip()
        elif line.startswith("### "):
            cid, _, ctitle = line[4:].partition("·")
            cur = {"id": cid.strip(), "title": ctitle.strip(), "doc": doc, "doc_title": title, "page": page,
                   "applies": applies, "route": "any", "trigger": None, "text": ""}
            units.append(cur)
        elif cur is not None and line.startswith("route:"):
            cur["route"] = line.split(":", 1)[1].strip()
        elif cur is not None and line.startswith("trigger:"):
            cur["trigger"] = re.compile(line.split(":", 1)[1].strip(), re.I)
        elif cur is not None and line.strip():
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
    return units


class PolicyTree:
    def __init__(self, units: list[dict]):
        import bm25s
        from .jev import model as jev_model  # share Jev's multilingual encoder: one model in memory
        self.units, self.enc = units, jev_model().enc
        texts = [f"{u['page']}: {u['title']}. {u['text']}" for u in units]
        self.pages = list(dict.fromkeys((u["doc"], u["page"]) for u in units))  # document order
        self.page_of = np.array([self.pages.index((u["doc"], u["page"])) for u in units])
        self.vecs = self._emb(texts)
        self.bm25 = bm25s.BM25()
        self.bm25.index(_tok(texts), show_progress=False)
        self.page_bm25 = bm25s.BM25()
        self.page_bm25.index(_tok([" ".join(t for t, p in zip(texts, self.page_of) if p == i) for i in range(len(self.pages))]),
                             show_progress=False)

    def _emb(self, texts):
        e = self.enc.encode(texts)
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)

    def search(self, query: str, category: str | None, k: int = 3) -> list[dict]:
        idx = np.array([i for i, u in enumerate(self.units) if "all" in u["applies"] or category in u["applies"]])
        q = self._emb([query])[0]
        unit = _z(self.vecs[idx] @ q) + 0.5 * _z(self.bm25.get_scores(_tok([query])[0])[idx])
        pages = sorted(set(self.page_of[idx]))
        best = np.array([unit[self.page_of[idx] == p].max() for p in pages])
        ps = _z(best) + 0.5 * _z(self.page_bm25.get_scores(_tok([query])[0])[pages])
        pos = {p: j for j, p in enumerate(pages)}
        nb = np.array([np.mean([ps[pos[x]] for x in (p - 1, p + 1) if x in pos and self.pages[x][0] == self.pages[p][0]]
                               or [ps[j]]) for j, p in enumerate(pages)])
        ps = ps + 0.3 * nb
        score = unit + np.array([ps[pos[self.page_of[i]]] for i in idx])
        top = np.argsort(-score)[:k]
        return [self.cite(self.units[idx[j]], float(score[j])) for j in top]

    @staticmethod
    def cite(u: dict, score: float | None = None) -> dict:
        c = {"id": u["id"], "title": u["title"], "doc": u["doc_title"], "section": u["page"], "text": u["text"],
             "route": u["route"]}
        return c | ({"score": round(score, 3)} if score is not None else {})



@lru_cache(maxsize=1)
def clauses() -> list[dict]:
    return [u for p in sorted(DIR.glob("*.md")) if p.name != "README.md" for u in parse(p)]


@lru_cache(maxsize=1)
def tree() -> PolicyTree:
    return PolicyTree(clauses())


def triggered(text: str, category: str | None) -> list[dict]:
    """Human-only clauses whose condition matches the customer's words (no model needed)."""
    return [PolicyTree.cite(u) for u in clauses() if u["route"] == "human" and u["trigger"] is not None
            and ("all" in u["applies"] or category in u["applies"]) and u["trigger"].search(text)]
