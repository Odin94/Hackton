# cognee/iter changelog

Running log for the long-lived `cognee/iter` branch. One block per iteration, newest on top. Each block: one-line summary, rationale, files touched, follow-ups.

Conventions:
- Code-only work until OpenRouter key is plugged in — no live cognify/quiz runs, so no live acceptance checks yet.
- Spec deltas (proposed changes to `notes/spec-cognee.md`) are queued in `notes/spec-deltas.md` — not applied to the spec directly.
- Scope fence: `backend/app/`, `backend/scripts/`, `backend/seed/`, `backend/tests/` (new). Agent loop, frontend, top-level config untouched.

---

## Iteration 4 — quiz quality + robustness (82 tests, all passing)

- **Quiz prompt rewritten:** now enumerates grounded-only / question-type diversity / anti-restatement / 1–2 sentence answers / JSON schema rules. Demo-visible quality lift at zero additional cost. Prompt stays small (~200 tokens).
- **`source_ref` extraction robust to three chunk shapes:** `{"is_part_of": {"name": ...}}` (dict form — happy path), `{"is_part_of": "flat-name"}` (string form), and typed `DataPoint`-style objects (`chunk.is_part_of.name`). Added `_chunk_text` helper using the same triple-shape pattern for text extraction. Verified against cognee's `ChunksRetriever` source and `DocumentChunkWithEntities` payload schema — payload flows through the vector engine which can return either dict or typed form depending on the backend.
- **`types.py` bounds:** `DiaryEntry.text` [1, 20000] chars, `DiaryEntry.tags` ≤ 32 items, `Material.source` [1, 512], `Material.course` [1, 64]. Catches pathological input at model construction, not just at HTTP boundary — protects the seed CLI and agent-loop call paths too.
- **Tests:** +8 service tests covering the three chunk shapes and the new helper unit behavior; +8 type tests for the new Pydantic bounds. Full suite 82 tests, 0.3s.
- Logged deltas 4, 5, 6 in `notes/spec-deltas.md`.

## Iteration 3 — test suite (65 tests, all passing)

- `backend/tests/` scaffolded; `pyproject.toml` adds pytest + pytest-asyncio + httpx under `[dependency-groups].dev` and configures `asyncio_mode = "auto"`.
- `test_cognee_service.py` (39 tests): sanitizer, retryability classifier, add_diary/add_material body formatting, cognify state machine, cognify lock serialization (same-dataset), cognify cross-dataset concurrency, query joining + single-hit unwrap (str/dict), index_status snapshot semantics, reset prune calls, full generate_quiz matrix — happy path, empty chunks, single-dict normalization, missing lineage, over-n truncation, under-n warning, one-shot retry on bad JSON, give-up after second retry, retry on malformed item shape, input validation.
- `test_routes.py` (20 tests): /health; POST happy paths; Pydantic 422s for bounds + missing fields + invalid dataset; error mapping matrix for ValueError → 400, CogneeServiceError non-retryable → 500, retryable → 503, unknown → 500; index-status snapshot.
- `test_seed.py` (6 tests): filename date + course parsers.
- All 65 tests run in 0.4s via `uv run pytest`. No network, no filesystem writes.

## Iteration 2 — lowest-risk hardening pass

- **Routes:** Pydantic bounds on `QuizReq.n` (1–20), `QuizReq.topic` (≤200), `QueryReq.q` (1–2000). Cognify now adds its task to a module-level `set` with a done-callback, preventing early GC. Added `GET /health`.
- **Service:** `_wrap` now also classifies `asyncio.TimeoutError` / `TimeoutError` / `ConnectionError` instances as retryable, beyond the substring heuristic. `generate_quiz` now performs an in-function one-shot retry on malformed JSON (and on malformed item shape), matching spec §9 intent — previously the spec said "caller retries once" but no caller did. Also: truncate to `n` on over-delivery, warn on under-delivery.
- **Seed:** `_parse_diary_date` pulls `YYYY-MM-DD` prefix from diary filenames into `DiaryEntry.ts` (UTC midnight); `_parse_course` pulls `ml-l3` → `ML-L3` from material filenames. Manifest save wrapped in `try/finally` so partial progress survives errors.
- Logged 3 spec-deltas in `notes/spec-deltas.md` — quiz retry location, `/health` addition, HTTP bounds.

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
