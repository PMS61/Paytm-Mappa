# Final: Sarvam clarity endpoints

**What it does.** Sarvam opens a clarity session (`POST /api/clarity/sessions` → `{"id"}`), then sends each new piece
of what the customer says (`POST /api/clarity {id, information}`). The Sarvam LLM, given the scenario so far and the
matching policy clauses, either asks for clarifications, escalates (call cap or critical), or concludes. On conclusion
the session is `done`, the conclusion is saved and a ticket goes through ghruank's existing pipeline (clarity → Jev →
autopilot / human queue). Manual tickets are unchanged.

**Run.** `uv venv -p 3.12 && uv pip install -e . pytest httpx`, `python -m mappa.train_jev` (once, ~3–5 min),
`SARVAM_API_KEY=... uvicorn mappa.api:app --port 8000`. Test: `.venv/bin/pytest -q` (61 pass).

**Only you can do.** Set `SARVAM_API_KEY` and run one real clarity session: the live Sarvam call is untested (no key
here), and the prompt may need tuning on real Hindi/Hinglish conversations. Decide the two open questions (should
"customer asks for a human" escalate; should escalate also raise a human-queue ticket). Commit / push when ready
(branch `sarvam-clarity`, nothing committed).

**Missing.** Auth, multi-process safety, LOW backlog items (docs/BACKLOG.md).
