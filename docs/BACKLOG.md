# Backlog (LOW findings)

## Phase 1
- store.py (ghruank's ticket store) has the same single-process lock and fixed `.tmp` name that sessions.py had. Run one uvicorn worker, or port the sessions.py fix.
- sessions.py: no fsync before replace.
- sessions.py: a corrupt or empty session file gives a bare JSONDecodeError (500 on every route until repaired by hand).
- sessions.py: save() on an unknown id silently recreates the session.
- schema.py ClaritySession: confidence has no 0..1 bound; calls has no ge=0.
- sarvam.py: the fence regex is case-sensitive (```JSON) and does not handle prose before the fence.
- sarvam.py: the HTTPError response is never closed; the timeout applies per socket operation; the reply body is read unbounded.
- The session store is rewritten on every save and never pruned.
- clarify.py: the sanitise secret flag is discarded, so ticket signals may lose secret_removed.
- clarify.py: the conclusion is saved before the missing-raised_by escalation.
- clarify.py: if the pipeline fails after store.next_id, the session stays open and a ticket id is consumed.
- train_jev is not bit-reproducible here (Python 3.12): route acc 91.2% vs the README's 92.3%, deployed missed-human 2.4% vs 1.9% in-distribution. results/jev.json left as committed.
- clarify.py: the module-level per-session lock dict grows with every session and is never pruned.
- GET /api/clarity/{id} has no auth and returns raised_by phone and transcript; ids are 32-bit random (CLR- + 8 hex). Add auth before exposing publicly.
- README Layout block columns are misaligned.
- policies/settlement.md P-SET-05 trigger misses common Hinglish ("naye phone se login hua"): it only matches `naya`, not `naye`/`nayi`. Same risk in other triggers. (ghruank's policies)
