# cognee/iter changelog

Running log for the long-lived `cognee/iter` branch. One block per iteration, newest on top. Each block: one-line summary, rationale, files touched, follow-ups.

Conventions:
- Code-only work until OpenRouter key is plugged in — no live cognify/quiz runs, so no live acceptance checks yet.
- Spec deltas (proposed changes to `notes/spec-cognee.md`) are queued in `notes/spec-deltas.md` — not applied to the spec directly.
- Scope fence: `backend/app/`, `backend/scripts/`, `backend/seed/`, `backend/tests/` (new). Agent loop, frontend, top-level config untouched.

---

## Iteration 0 — branch setup

- Created `cognee/iter` branched off `main@502b53a`.
- Scaffolded this log and `notes/spec-deltas.md`.
- Next up: audit pass to enumerate concrete quality targets.
