# cognee/iter changelog

Running log for the cognee memory layer. 16 iterations landed on `main` (originally on long-lived `cognee/iter`; merged and deleted after iter 15). Newest on top.

Conventions:
- Spec deltas (proposed changes to `notes/spec-cognee.md`) are queued in `notes/spec-deltas.md` — not applied to the spec directly.
- Push directly to main now (since iter 15). Rebase (never merge) when teammates land commits.
- Scope fence: `backend/app/`, `backend/scripts/`, `backend/seed/`, `backend/tests/`. Agent loop (`backend/agent/` — Odin's), frontend (Amin's) untouched unless coordinated.

---

## Iteration 18 — remove orphaned `backend/quiz/` (133 tests)

- **Deleted `backend/quiz/generator.py` + `__init__.py`.** Never wired: no route imported it, no code called it. Was a parallel quiz implementation from an earlier commit (hardcoded mock schedule, `instructor` + gpt-4o-mini, no cognee). Dead module cleanup, not a behavior change.
- **`instructor` dep unchanged** — it stays as a transitive via cognee, not our direct dep.
- **Demo direction captured:** user accepted diary-fabrication risk for demo visibility. Fix is B2 (flip `ENABLE_BACKEND_ACCESS_CONTROL=true`) post-demo. See `project_hackton_demo_fabrication.md` memory.
- **Tests:** 133 passing, 0 lint. (Jump from 119 → 133 is agent-side tests accumulated on main since iter 17's snapshot — not new cognee-layer tests.)

---

## Iteration 17 — large-PDF fix + full 3-course corpus ingest (119 tests)

**Root cause fixed:** Cognee's `upsert_edges` issues one `INSERT … VALUES (…), (…), …` for all edges in a batch. SQLite caps SQL variables at 32 766; 11 columns/row → max ~2 978 rows per INSERT. Dense CS-textbook PDFs exceed this even at 10 chunks/batch.

- **Fix:** Edited `.venv/…/cognee/modules/graph/methods/upsert_edges.py` directly to split `edges_to_add` into 2 000-row sub-batches before executing. Monkey-patching via `config.py` was attempted but didn't survive the import chain in the seed process — direct venv edit is the reliable approach.
- **`COGNEE_CHUNKS_PER_BATCH=100`** restored (was reduced to 10 as a workaround; no longer needed). Full corpus cognifies in ~80s instead of the projected 4–9 hours.
- **CORS added** to `main.py` (`CORSMiddleware, allow_origins=["*"]`) — was blocking Amin's frontend.
- **`--skip-courses` flag** on `seed ingest` — `Einführung_in_die_Rechnerarchitektur` (126 MB, 10 monster PDFs up to 24 MB) excluded from demo corpus. Remaining 3 courses: DS + EI + Analysis, 28 PDFs, 1356 pages, 11 MB.
- **Progress tracking in `live_smoke`:** pre-cognify page count + ETA estimate; elapsed time after cognify completes.
- **DB wipe gotcha corrected:** `find .cognee_system -type f -delete` leaves empty dirs that break LanceDB on next run. Correct command: `find .cognee_system -delete` (removes files AND dirs).
- **Full end-to-end verified:** 28 files ingested (0 failed), cognify in 79.6s, quiz on "graphs" returns 3 grounded DS questions with `source_ref='Diskrete_Strukturen'`. 119 tests passing, 0 lint.

**Known issue (pre-existing):** With full materials corpus loaded, diary isolation degrades — empty-diary query returns fabricated content instead of `NoDataError`. Prompt-level isolation (iter 15) isn't sufficient when the shared graph is large. Not demo-blocking for quiz flow.

---

## Status snapshot

- **Repo state:** `main` at iter 18 (orphaned `backend/quiz/` removed). Frontend rebuild happening on a separate branch — scope-fence still holds.
- **Tests:** 133 passing (`cd backend && uv run pytest`, ~1.4s — 107 cognee-layer + 26 agent-side from Odin). `uv run ruff check` clean.
- **Live verified:** Full 3-course corpus (DS + EI + Analysis, 28 PDFs, 1356 pages) ingested + cognified in ~90s. Quiz on "graphs" returns 3 grounded DS questions with correct source_ref. Diary isolation degraded with full corpus (see open threads).
- **Live smoke harness:** `backend/scripts/live_smoke.py` — `uv run python -m scripts.live_smoke [--pdf PATH] [--course NAME] [--topic TOPIC] [--n N] [--skip-ingest]`. Now prints page count + ETA before cognify and elapsed time after.
- **Corpus on disk:** 42 PDFs. 28 ingested (DS + EI + Analysis). ERA (10 PDFs, 126 MB, up to 24 MB each) excluded — too slow to cognify for demo.
- **Diary:** 33 narrative entries in `data/diary/` (Sep 22 – Nov 13 2025). Persona: Odin, CS student, AI-engineering focus (curious drifter). Demo moment: Nov 13 formal languages lecture, TumTum pings post-lecture. Cognee pre-seeded on demo machine. `seed.sh` at repo root resets + ingests + cognifies (~5 min).
- **Spec deltas:** 14 queued in `notes/spec-deltas.md`. None applied to the spec file itself.

## Open threads (things that opened but didn't close)

- **ERA corpus excluded.** `Einführung_in_die_Rechnerarchitektur` (10 PDFs, 126 MB, up to 24 MB) skipped via `--skip-courses`. Individual files would take hours to cognify. Option: postgres backend, pre-split, or just exclude permanently for demo.
- ~~**Odin's parallel `/quiz/generate`**~~ — removed in iter 18. `backend/quiz/` deleted; it was orphaned (no route ever wired it, no importer). Our `/quiz` (cognee-grounded) is the sole quiz path.
- ~~**CORS missing in `main.py`.**~~ Fixed in iter 17.
- **Diary isolation degraded with full corpus.** Empty-diary query returns fabricated content instead of `NoDataError` once 28 PDFs are cognified. Prompt-level isolation (iter 15) insufficient when shared graph is large. Options: `ENABLE_BACKEND_ACCESS_CONTROL=true`, stronger prompt, or accept (quiz flow unaffected).
- **Quiz quality on off-topic prompts.** gpt-4o-mini doesn't refuse when the topic has no material; grounds loosely in whatever the retriever returns. Prompt already says "strictly grounded". Options: stronger refusal instruction, explicit "return empty if no relevant content" in the tool schema.
- **Quiz under-delivery not handled.** If the LLM returns fewer items than `n`, we log a warning and return what we got. Frontend shows "3 questions" when the user asked for 5. Options: retry to fill, re-request, or error out.
- **`add_diary_entry` still uses raw-string ingest** (unfixed from iter 12). Document.name is `text_<md5>.txt`, not a date. Doesn't matter for quiz (materials-only) but breaks any future diary citation.
- **Page-level citation for PDFs** — pypdf gives "Page N:" markers, cognee's chunker splits by tokens ignoring them. A quiz item can span multiple pages; we can't tell which.

## Environment-specific gotchas (land-mines to remember)

- **Fresh cognee DB can't migrate.** `cognee.run_startup_migrations()` crashes on `ab7e313804ae_permission_system_rework` → `NoSuchTableError("acls")`. Solved: `main.py` wraps in try/except, pipeline `setup()` creates schema lazily on first `add/cognify`.
- **`.cognee_system/` needs auto-mkdir.** Solved in `app.config`; cognee doesn't create the parent dirs.
- **Claude Code's `ALL_PROXY=socks5h://…` breaks httpx** (no `socksio` installed). Solved in `app.config` (popped at import).
- **Ruff import sort breaks config ordering.** Solved by putting `from app import config` in `app/__init__.py` — ruff can't reorder across files.
- **`EMBEDDING_PROVIDER=openai_compatible` hits a cognee bug** (missing `max_completion_tokens` on the engine). Use `EMBEDDING_PROVIDER=openai` + `EMBEDDING_MODEL=openai/text-embedding-3-small` for OpenAI direct via LiteLLM.
- **Chunks search returns `belongs_to_set`, NOT `is_part_of`.** Source lineage is whatever we pass via `node_set=[…]` at ingest.
- **Dataset isolation is fictional without `ENABLE_BACKEND_ACCESS_CONTROL=true`.** We went with AC-off + prompt-level isolation (iter 15). Acceptable for demo.
- **Clean-slate DB required after a crash.** Cognee leaves corrupted empty `.lance` table dirs that LanceDB treats as broken tables (not missing ones). `find backend/.cognee_system -type f -delete` is NOT sufficient — it leaves empty dirs. Use `find backend/.cognee_system -delete` (removes files AND dirs) or equivalent.

---

## Iteration 16 — live run + environment hardening (107 tests)

**First end-to-end run** against real OpenRouter + OpenAI. A 50KB PDF (`folien-11a.pdf` from the Einführung_in_die_Informatik corpus) ingested + cognified + quizzed successfully. Along the way 9 gaps surfaced; all fixed on main.

- **`source_ref` restored as the course label.** Probed cognee's live chunk payload: `is_part_of` isn't in the default `IndexSchema` payload. But `belongs_to_set` is — it carries the `node_set=[course]` we attach at ingest. `_extract_source_ref` now reads `belongs_to_set[0]` first, falls through to `is_part_of` for engines that do expose it. End-to-end: `QuizItem.source_ref == 'Einführung_in_die_Informatik'` ✓.
- **Typed error for missing dataset.** Added "no datasets found" / "datasetnotfounderror" to `_NO_DATA_MARKERS`; empty-diary query now surfaces as `NoDataError(retryable=True)` instead of generic CogneeServiceError.
- **`app/__init__.py` imports `app.config` as a package-level side-effect.** Iter-10's ruff import-sort had reordered `import cognee` above `from app.config import settings` in `cognee_service.py`, causing cognee to cache its `base_config` defaults (pointing into the venv) before our env mutation ran. Package init is below ruff's reach.
- **`app/config.py` creates cognee's system dirs + pops SOCKS proxy env vars.** `.cognee_system/{data,system/databases,cache}` must exist before cognee's first SQLite open. And Claude Code's `ALL_PROXY=socks5h://…` without `socksio` installed breaks httpx startup — cleared at import time so `HTTPS_PROXY` takes over.
- **`main.py` try/excepts `cognee.run_startup_migrations()`.** Cognee's alembic migration `ab7e313804ae` raises `NoSuchTableError("acls")` on fresh relational DBs (it reads column metadata before base tables exist). Schema is created lazily by the pipeline's own `setup()` on first add/cognify, so boot failure is recoverable — log and continue.
- **`.env` / `.env.example` / README embedding config corrected.** `EMBEDDING_PROVIDER=openai` + `EMBEDDING_MODEL=openai/text-embedding-3-small` works via LiteLLM. The `openai_compatible` path is attractive but hit by a cognee bug — `OpenAICompatibleEmbeddingEngine` doesn't expose `max_completion_tokens`, which `get_max_chunk_tokens` requires during cognify.
- **Tests:** +4 (`_extract_source_ref` from `belongs_to_set`, empty-`belongs_to_set` fallback, end-to-end `source_ref` on `generate_quiz`, `DatasetNotFoundError → NoDataError` in `_wrap`). 107 passing, 0 lint.

**What's still unsolved:**
- **Large PDFs blocked.** `DS_2023_Script_v1.pdf` (4 MB, Diskrete Strukturen) fails cognify with `sqlite3.OperationalError: too many SQL variables` — cognee's bulk edge-insert exceeds SQLite's placeholder ceiling. Options: postgres (biggest change), smaller chunks (config tweak), pre-split PDFs (offline step). Needs investigation before we can ingest the full 42-PDF corpus.
- **Quality of quiz on arbitrary topics.** With `folien-11a.pdf` (about Java polymorphism) the quiz topic "boolean logic" produced questions about `instanceof`/subclassing rather than refusing for lack of material. The system prompt says "strictly grounded" but gpt-4o-mini interpreted that loosely. Not a correctness bug — the questions ARE grounded in what the retriever returned — but demo-floor awkward.

## Iteration 15 — prompt-level dataset isolation (103 tests)

Reversed the AC-on flip from iter 12 in favor of prompt-level isolation. "Reliable and simple" preference — AC-on is cognee's multi-tenant path with unknown edge cases in single-user mode, AC-off is the proven single-user path we've been running on.

- **`.env` / `.env.example`:** `ENABLE_BACKEND_ACCESS_CONTROL=false`. Single shared graph.
- **`_DATASET_SYSTEM_PROMPTS` constant** maps each dataset to an explicit "ignore cross-dataset context" instruction. `_query(dataset, q)` passes the matching prompt via `system_prompt=` kwarg on `cognee.search` (verified cognee's `search()` accepts this kwarg at `search.py:34` and that it takes precedence over `system_prompt_path` at the retriever level).
- **Quiz system prompt strengthened** to explicitly tell `emit_quiz` to ignore any context that reads like a journal entry.
- **Tests:** +2 (`test_query_{diary,materials}_passes_{diary,materials}_system_prompt`) pin the prompt wiring. 103 passing, 0 lint.
- Updated delta 11 to reflect the reversal and reasoning.

**Known limit:** CHUNKS retrieval (for quiz) doesn't run an LLM — chunks can be cross-contaminated at retrieval time. Semantic bias (diary entries are short personal notes vs lectures being long technical prose) mostly prevents this; prompt instruction is the backstop.

## Iteration 14 — quiz → tool-use mode (101 tests)

Preempts the "LiteLLM strips `response_format` on OpenRouter" (#4 from my audit) before first live run. Bug fires per-model and per-version — rather than verify-and-pray, I moved to the function-calling path that cognee's internal calls already use.

- **`_quiz_llm_call` rewritten:** passes `tools=[{type:function,function:{name:"emit_quiz",…_QUIZ_SCHEMA}}]` + `tool_choice=...forces emit_quiz...`. Parses `response.choices[0].message.tool_calls[0].function.arguments` (accepts str or pre-parsed dict for LiteLLM backend variance).
- **Failure modes map cleanly to `MalformedLLMResponseError`:** (a) `tool_calls` missing (model refused to use the tool), (b) arguments not parseable JSON, (c) `items` shape wrong. Existing retry loop handles all three; after two refusals, 503.
- **Removed `extra_body={"response_format":…}` hack** entirely — no longer traversing the buggy LiteLLM path at all.
- **Tests:** +3 (`test_generate_quiz_surfaces_missing_tool_call_as_malformed`, `test_generate_quiz_accepts_pre_parsed_tool_arguments`, `test_generate_quiz_uses_function_calling_not_response_format`). Existing tests still pass — the `llm_response()` helper in `conftest.py` was updated to build tool-call shaped responses, so existing tests transparently use the new path. 101 passing, 0 lint.
- Logged as delta 13.

**#4 moved from "needs verification" to "replaced with a known-good path"** — cognee uses tool-call mode internally for structured output, so this is the same code path as the rest of our stack. No separate verification needed.

## Iteration 13 — multi-course corpus + PDF ingest (98 tests)

Amin pushed the real study materials: 42 PDFs across 4 courses in `/data/<course>/materials/`. Plumbing updated.

- **`add_material_from_file(path, course)`** new entry point. Hands the absolute path directly to `cognee.add(..., node_set=[course])` — no tempfile write, no body mutation. Cognee picks the right loader: `PyPdfLoader` (pypdf already installed as cognee dep) for `.pdf`, text loader for `.md`/`.txt`. `Document.name` preserved for all three, so `QuizItem.source_ref` shows the real filename.
- **Seed CLI restructured** (`scripts/seed.py`). `cmd_ingest` now auto-detects two layouts via the new `_find_material_dirs(root)` helper:
  - Flat: `<root>/materials/…` → course = `"seed"`.
  - Course-nested: `<root>/<course-name>/materials/…` → course = dir name.
  Diary stays flat at `<root>/diary/`. Hidden dirs (`.git`, `.venv`) skipped. Manifest digest now computed from raw bytes so PDFs round-trip SHA256 correctly.
- **`SUPPORTED_MATERIAL_SUFFIXES = {".md", ".txt", ".pdf"}`** — diary stays `.md`/`.txt` only.
- **Existing `add_material(Material)`** unchanged semantically, plus `node_set=[material.course]` for consistency.
- **Tests:** +7 new (`_find_material_dirs_*` × 4, `test_cmd_ingest_flat_layout`, `test_cmd_ingest_nested_layout_with_pdfs`, `add_material_from_file_*` × 4, `test_cmd_ingest_no_materials_dir_processes_diary`). Updated existing `add_material` tests to use `**kwargs` capture. 98 passing, 0 lint.
- **README** + spec delta 12 logged.

## Iteration 12 — critical bug fixes from audit (91 tests)

Two confirmed-broken issues, fixed:

- **#1 source_ref was gibberish.** Traced: `cognee.add(data=string, dataset_name=…)` routes through `save_data_to_file` which generates `text_<md5>.txt` as the Document name when no filename is provided (`ingestion/save_data_to_file.py:19-22`). So every `QuizItem.source_ref` was that md5 string — spec §10 item 4 was nominally passing but producing useless output. Fix: `add_material` writes the body to a per-call tempdir under a sanitized `material.source` filename, then passes the absolute path to `cognee.add`. Cognee's `save_data_item_to_storage` detects abs-path strings → returns `file://` URI → loader extracts the real filename → `data_point.name` is preserved end-to-end (`ingest_data.py:153`). Tempdir is isolated per-call (no collision on concurrent same-`source` ingests) and cleaned up in `finally`. New `_safe_filename` helper sanitizes path-traversal attempts (`../../etc/passwd` → `etc_passwd`).
- **#3 dataset isolation was fictional.** Traced: `ENABLE_BACKEND_ACCESS_CONTROL=false` (our prior default) routed cognee's `search_in_datasets_context` through the `else` branch that calls `get_retriever_output(dataset=None, …)` (`modules/search/methods/search.py:304-327`) — a single unfiltered query across the whole graph. The `datasets=["diary"]` arg was ignored. Fix: flip the flag to `true` in `.env` / `.env.example`. Verified our LanceDB+Kuzu setup is in cognee's multi-user-supported lists (`context_global_variables.py:94-95`) and that `add`/`cognify` honor the flag via the pipeline's `set_database_global_context_variables` call. Enables per-dataset DB files — real storage isolation. **Needs live-run confirmation.**
- **Tests:** +3 for `add_material` (file-path ingest, path-traversal sanitization, tempdir-cleanup-on-failure). Updated existing "header line" test that asserted against raw-body `data`. Full suite 91 passing, 0 lint.

Logged as deltas 10 and 11.

**Still unverified — needs live run:**
- #4 LiteLLM `response_format` stripping on OpenRouter (our `extra_body` hack may or may not survive).
- #6 cognee's entity extractor parsing the `Topics: …` trailing sentence as intended.
- #3 fix itself — does the flag actually isolate in single-user mode end-to-end.

## Iteration 11 — typed error hierarchy + quiz simplification (89 tests)

**Motivation:** "reliable and simple" — live acceptance blocked on materials, so focus on things that improve both without needing real data.

- **Typed errors:** Added `NoDataError`, `LLMTimeoutError`, `MalformedLLMResponseError`, `UpstreamRateLimitError`, `UpstreamError` as `CogneeServiceError` subclasses. `_wrap()` pattern-matches exception message + class to return the most specific subclass (falls back to base for unknown). Callers can branch on type instead of string-matching `detail`. HTTP error mapping unchanged (all subclasses still `isinstance(CogneeServiceError)`).
- **Quiz call-graph flattened:** Merged `_generate_quiz_inner` + `_quiz_llm_call_with_retry` into `generate_quiz`. Kept `_quiz_llm_call` as the single-attempt helper. Retry loop is now inline and only targets `MalformedLLMResponseError` (previously retried on any retryable error — timeouts were retried at the same budget, which doesn't help).
- **Error-site routing:** `generate_quiz` now raises `NoDataError` for empty-chunks / textless-chunks (was base class), `LLMTimeoutError` on `asyncio.wait_for` timeout (was base class), `MalformedLLMResponseError` on JSON/shape failure (was base class). Agent loop can now distinguish "cognify hasn't run" from "LLM flaked" without string parsing.
- **Tests:** +1 (`test_all_subclasses_are_cognee_service_error`); several existing tests tightened to `pytest.raises(NoDataError)` / `LLMTimeoutError` / `MalformedLLMResponseError` instead of the base class. 89 passing, 0 lint.

Logged as delta 9 in `notes/spec-deltas.md`.

## Iteration 10 — ruff lint gate (88 tests, 0 lint issues)

- Added `ruff>=0.6` to dev deps and a minimal `[tool.ruff]` config in `pyproject.toml`: line length 100, target py312, rules `E F I UP B SIM`, ignores `E501`/`B008`/`SIM117` as situational false-positives.
- Ran `uv run ruff check --fix` to auto-apply 21 safe fixes across 6 files: import sort (I001), `datetime.timezone.utc` → `datetime.UTC` (UP017), `typing.AsyncIterator` → `collections.abc` (UP035), bare `asyncio.TimeoutError` → builtin `TimeoutError` alias (UP041).
- Manually fixed one B007 (unused loop var) by replacing an `(field, kwargs)` tuple iteration with a bare list-of-kwargs, which was clearer anyway.
- `uv run ruff check` now returns clean; tests still 88 green.
- Gives Odin/Amin a working lint target without imposing formatter churn.

## Iteration 9 — OpenAPI polish (88 tests)

- Every route has a `summary=`, a short docstring (surfaces as route description in `/docs`), and a `tags=` grouping (`ingest` / `index` / `query` / `health`). `/docs` page now has clean collapsible sections instead of a flat list.
- Module-level docstring on `routes_cognee.py` and `cognee_service.py` summarizing purpose and where tests live.
- No behavioral change.

## Iteration 8 — guard empty-text chunks (88 tests)

- `generate_quiz` now raises `CogneeServiceError("…had no text content", retryable=True)` when `chunks` is non-empty but every chunk has blank text. Previously we'd have sent the LLM an empty context and let it hallucinate freely.
- Retryable flag is set because the vector index might be mid-write; a second attempt may pick up freshly-indexed chunks with text.
- Added test for the edge case.

## Iteration 7 — LLM timeout → Settings (87 tests, still passing)

- Moved `_LLM_TIMEOUT_SECONDS` (module constant) to `Settings.llm_call_timeout_seconds`. Env override is `LLM_CALL_TIMEOUT_SECONDS` (deliberately distinct from any cognee-internal `LLM_TIMEOUT` — our timeout caps *our* LiteLLM call only, and mixing the two risks fighting cognee's retry logic).
- Updated `cognee_service._quiz_llm_call` to read from `settings`, and the test to monkeypatch the setting instead of the module constant.
- `.env.example` documents the new knob with its default.

## Iteration 6 — seed resilience + backend README (87 tests)

- **`cmd_ingest` continues past per-file errors.** Previously a single failing `cognee.add` aborted the whole run; now failures are logged, collected, and summarized; the manifest is still saved and the process exits non-zero if anything failed. Successful files still get recorded, so re-runs resume where they left off.
- **Consistent logging.** Remaining `print()` calls in `seed.py` swapped to `log.info` / `log.warning` / `log.error`. Matches `cognee_service`'s log style.
- **Added `backend/README.md`** (was empty). Covers setup, run, seed CLI, route table with bounds + error mapping, env vars, test commands, troubleshooting cheat-sheet. Demo-presentation win and a landing page for Odin/Amin.
- **Tests:** +4 integration-style `cmd_ingest` tests using `tmp_path` — happy path with per-file mock assertions, SHA256 skip-on-rerun, continue-past-single-file-error with manifest verification, missing-subdir tolerance. Full suite 87 tests, 0.37s.

## Iteration 5 — response models, LLM timeout, duration logging (83 tests)

- **Response models:** every route declares `response_model=...` (`StatusResp`, `AnswerResp`, `QuizResp`, `IndexStatusResp`). FastAPI's `/docs` now shows the typed envelope; no free-form `dict` anywhere. Wire shape unchanged — fully backward-compatible with the frontend.
- **LLM timeout:** `_quiz_llm_call` now wraps `litellm.acompletion` in `asyncio.wait_for` with a 30s cap. Timeouts surface as `CogneeServiceError(retryable=True)`, which the retry loop respects. Prevents a stuck provider from wedging the quiz endpoint — demo safety.
- **Duration logging:** added an `_timed` async context manager; `cognify_dataset`, `_query`, `generate_quiz` all log `label start …` / `label done … elapsed_ms=N` at INFO. Makes the demo's latency budget visible without a metrics stack.
- **Tests:** +1 for the timeout path (monkeypatches `_LLM_TIMEOUT_SECONDS` to 0.01s; verifies retryable error).
- Logged deltas 7, 8 in `notes/spec-deltas.md`.

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
