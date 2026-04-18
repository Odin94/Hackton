# cognee/iter changelog

Running log for the long-lived `cognee/iter` branch. One block per iteration, newest on top. Each block: one-line summary, rationale, files touched, follow-ups.

Conventions:
- Code-only work until OpenRouter key is plugged in — no live cognify/quiz runs, so no live acceptance checks yet.
- Spec deltas (proposed changes to `notes/spec-cognee.md`) are queued in `notes/spec-deltas.md` — not applied to the spec directly.
- Scope fence: `backend/app/`, `backend/scripts/`, `backend/seed/`, `backend/tests/` (new). Agent loop, frontend, top-level config untouched.

---

## Iteration 1 — audit findings (no code changes)

Enumerated quality targets across `backend/app/`, `backend/scripts/`, `backend/seed/`. Nothing edited yet — this pass is diagnostic.

**cognee_service.py**
- `_wrap` classifies retryability by substring match on the exception message; class-level checks (`asyncio.TimeoutError`, `TimeoutError`) are missing → a bare `TimeoutError()` with no message would be classified as non-retryable.
- `generate_quiz` does not self-retry on malformed JSON; spec §9 says "caller retries once", but the HTTP route doesn't retry either — gap in practice.
- `generate_quiz` does not verify `len(items) == n`; LLM may return too few/many silently.
- `_query` joins results with `\n`; if cognee returns list-of-dicts (rare for GRAPH_COMPLETION but possible), `str(dict)` is ugly. Low priority.

**routes_cognee.py**
- `QuizReq.n` unbounded; `QueryReq.q` length unbounded. Cheap hardening.
- `post_cognify` ignores the `asyncio.create_task` return value (no reference held). In FastAPI this is fine in practice, but adding a set-based task registry is cleaner.
- No `GET /health` endpoint — trivial add and useful for frontend smoke-tests.

**seed.py**
- `cmd_ingest` aborts on any per-file error before saving manifest → partially-added files lose their manifest entry, causing duplicates on re-run. Needs `try/finally` around the save.
- Diary: `DiaryEntry(text=text)` discards the filename date (e.g. `2026-04-08-mon.md`). The temporal marker is core to the KG's value. Should parse `YYYY-MM-DD` from filename into `ts`.
- Materials: `course="seed"` is hardcoded. Filenames like `ml-l3-transformers.md` carry the course prefix — extract it.
- Materials: `source=path.name` loses the folder context. Fine for flat layout; revisit if nested.

**Tests**
- Zero. Biggest quality gap. Plan: pytest + pytest-asyncio, mock cognee/litellm at the module boundary.

**Out of scope (flagged but not mine)**
- Main has no CORS — frontend's concern.
- No healthcheck in routes_cognee — adding as quality win is fine since it's in scope.

Follow-ups queued as tasks.

## Iteration 0 — branch setup

- Created `cognee/iter` branched off `main@502b53a`.
- Scaffolded this log and `notes/spec-deltas.md`.
