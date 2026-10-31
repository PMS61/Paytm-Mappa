# Phase 1 report: clarity sessions over Sarvam

## Built
- `mappa/sarvam.py`: Sarvam chat client (JSON mode, 30 s timeout); every failure is a `SarvamError`, and the key never appears in errors.
- `mappa/sessions.py`: clarity session store `data/clarity_sessions.json` (fcntl cross-process lock, unique temp file + atomic replace). IDs are `CLR-` + 8 hex characters.
- `mappa/clarify.py`: `step(id, information)`. It appends the new information to the scenario, retrieves policy clauses with ghruank's search (`policies.tree().search`), and asks Sarvam for clarifications, a conclusion and a confidence. It returns needs_clarification, done (ticket raised through the pipeline) or escalate (call cap, LLM critical flag, human-only policy clause, injection pattern, amount ≥ ₹50,000). A per-session lock means one ticket per session.
- `mappa/pipeline.py`: the `POST /api/tickets` path (`raise_ticket`, `process`) moved out of api.py unchanged, so manual tickets and clarity "done" sessions share one path.
- `mappa/api.py`: `POST /api/clarity/sessions`, `POST /api/clarity {id, information}`, `GET /api/clarity/{id}`. Every response and error body has `id`.
- README section "Sarvam clarity endpoints"; `.gitignore` entries for the session store and its lock file.

## Tested
61 tests (`.venv/bin/pytest -q`), none calling the real Sarvam API. Live end-to-end run with a fake LLM: id → needs_clarification → done → TKT ticket (clarity + Jev + autopilot ran) → escalate on the cap and on a critical case; manual tickets still work.

## Bugs found and fixed
Cross-process session races; a cut-off Sarvam reply escaping as a raw error; crashes on oddly shaped LLM JSON; an OTP hiding an injection from the guard; "Rs 60,000" bypassing the ceiling; concurrent done calls raising two tickets; any internal KeyError reported as 404; 422 bodies echoing the caller's text.

## Known limits
Single process only (the per-session lock is in-memory, and ghruank's store.py is single-process). No auth on the clarity routes. The real Sarvam call has never run here (no key). LOW items are in docs/BACKLOG.md.
