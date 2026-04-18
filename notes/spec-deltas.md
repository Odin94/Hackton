# Spec deltas — cognee layer

Proposed changes to `notes/spec-cognee.md`, queued for user review. **Nothing here is applied to the spec file itself.** Each item is a concrete edit suggestion with rationale; user decides accept / reject / amend.

Format per entry:
- **Section** — which part of the spec
- **Change** — what to edit
- **Why** — motivation
- **Status** — `proposed` / `accepted` / `rejected`

---

## Delta 1 — quiz retry lives in `generate_quiz`, not in the caller

- **Section:** §3 service interface — `generate_quiz` / §9 risks (row: "LLM returns malformed JSON").
- **Change:** Replace "caller retries at most once" with "`generate_quiz` retries the LLM call once internally on `retryable=True`; after the second failure, raises `CogneeServiceError(retryable=True)` for the caller to handle."
- **Why:** "Caller retries once" was ambiguous in practice — the HTTP route in `routes_cognee.py` did not implement retry, so the spec's mitigation never fired. Pulling retry into the service keeps the contract self-enforcing and avoids every caller re-implementing the same pattern. The HTTP caller can still surface 503 on exhaustion.
- **Status:** proposed — implemented behaviorally on `cognee/iter` in `generate_quiz._quiz_llm_call_with_retry`. Spec text not yet amended.

## Delta 2 — `/health` endpoint

- **Section:** §4 HTTP routes.
- **Change:** Add `GET /health → {status: "ok"}`.
- **Why:** Trivial liveness endpoint — useful for frontend boot checks and for detecting config misconfiguration (since `config.py` raises at import, a responding `/health` proves env is wired).
- **Status:** proposed — implemented on `cognee/iter`. Spec table not yet amended.

## Delta 12 — `add_material_from_file` + PDF support + course-nested seed layout

- **Section:** §3 service / §5 seed CLI / §6 supported formats.
- **Change:** New `add_material_from_file(path, course)` that passes the file path directly to `cognee.add(..., node_set=[course])`. Cognee's native `PyPdfLoader` handles `.pdf` during ingest (pypdf is already a cognee dep — no new libs). `.md`/`.txt` use the text loader. `Document.name` is preserved for all three extensions.
- **Seed CLI:** `cmd_ingest` auto-detects flat (`<root>/materials/…`) and course-nested (`<root>/<course>/materials/…`) layouts. `.pdf` added to `SUPPORTED_MATERIAL_SUFFIXES`. Digest now bytes-based (supports binary).
- **Why:** Original spec §5 assumed flat markdown ingest. Real corpus is a four-course PDF dump under `data/<course>/materials/`. Routing to cognee's native loader keeps our layer simple — no offline pre-extract step.
- **`node_set=[course]` metadata:** attaches the course label so GRAPH_COMPLETION searches can filter on `node_name=[course]` later. CHUNKS search still ignores `node_name` (node_name verification from an earlier session), so course filtering at quiz time would require either switching quiz to GRAPH_COMPLETION (loses chunk lineage) or post-filter on chunk payload metadata.
- **Status:** implemented on `cognee/iter`. Needs live-run confirmation that cognee's PDF extraction cleanly handles all 42 PDFs in amin's corpus (some encrypted / OCR-scanned slides may fail per-file without aborting the run, thanks to iter-6 resilience).

## Delta 10 — `add_material` ingests as a file path (source_ref preservation)

- **Section:** §3 service / §10 acceptance item 4.
- **Change:** `add_material` now writes the material body to a tempdir under `material.source` as the filename, then hands cognee the file path. Was passing a raw string, which caused cognee's `save_data_to_file` to generate `text_<md5>.txt` as the Document name — making every `QuizItem.source_ref` meaningless gibberish.
- **Why:** Spec §10 item 4 requires `source_ref` to be populated with a filename from seeded materials. Verified: with raw-string ingest, the name is an md5 hash; with file-path ingest, `Document.name` is the actual filename (`cognee/tasks/ingestion/ingest_data.py:153`).
- **Status:** implemented on `cognee/iter`. `material.source` is sanitized to a filesystem-safe name before being used as the filename. Tempdir is isolated per-call (so concurrent ingests of the same `source` can't collide) and removed after `cognee.add` returns (including on failure).

## Delta 11 — Enable `ENABLE_BACKEND_ACCESS_CONTROL` for real dataset isolation

- **Section:** §1 datasets / §6 config.
- **Change:** `.env` and `.env.example` flip `ENABLE_BACKEND_ACCESS_CONTROL=false` → `true`. Spec §1 says "kept disjoint"; with the flag off, cognee's `search()` runs a single unfiltered query with `dataset=None` (`modules/search/methods/search.py:304-327`), so `datasets=["diary"]` is ignored and diary queries can pull materials-derived context (and vice versa).
- **Why:** LanceDB + Kuzu are both in cognee's `VECTOR_DBS_WITH_MULTI_USER_SUPPORT` / `GRAPH_DBS_WITH_MULTI_USER_SUPPORT` lists, so the flag materializes per-dataset database files — real storage-level isolation. The same flag routes `add`/`cognify` to the dataset-specific DB via the pipeline's `set_database_global_context_variables` call, so data-in and data-out stay consistent.
- **Risk:** not yet verified under a live cognify run. If single-user mode mis-behaves with the flag on, fallback is prompt-engineering isolation (tell the LLM "only consider diary entries" in query prompts).
- **Status:** flag flipped on `cognee/iter`. Needs live-run confirmation.

- **Section:** §3 service / §4 error mapping.
- **Change:** `CogneeServiceError` now has concrete subclasses: `NoDataError`, `LLMTimeoutError`, `MalformedLLMResponseError`, `UpstreamRateLimitError`, `UpstreamError`. Each sets a sensible `default_retryable`. `_wrap()` returns the specific subclass when the underlying exception matches a known pattern, otherwise the base class (treated as non-retryable). All subclasses still pass `isinstance(exc, CogneeServiceError)`, so §4 HTTP mapping continues to work without changes.
- **Why:** Callers (agent loop, frontend) can branch on `isinstance(e, NoDataError)` to emit "please wait for cognify" UX, rather than string-matching `detail`. Quiz retry logic is also cleaner — retry only on `MalformedLLMResponseError`, not on "any retryable" (which was eating timeouts).
- **Status:** proposed — implemented on main via cognee/iter. Spec text not yet amended.

## Delta 7 — LLM timeout + duration logging

- **Section:** §3 service / §6 config / §9 risks.
- **Change:** `generate_quiz` now wraps `litellm.acompletion` in `asyncio.wait_for` with a 30s default (module constant `_LLM_TIMEOUT_SECONDS`). Timeout raises `CogneeServiceError("LLM call exceeded …", retryable=True)`. Also: `cognify_dataset`, `_query`, and `generate_quiz` are now wrapped in a `_timed` context that emits `elapsed_ms` at INFO.
- **Why:** Previously a stalled provider could wedge the quiz endpoint indefinitely. Spec §9 row "cognify slow / blocks event loop" implicitly wanted this but didn't codify it. Duration logging helps diagnose slowness in demo without a full metrics stack.
- **Status:** proposed — implemented on `cognee/iter`. Constant is module-local; could be promoted to `Settings` if the user wants env override.

## Delta 8 — Typed response models on all routes

- **Section:** §4 HTTP routes.
- **Change:** Each route declares `response_model=...` (`StatusResp`, `AnswerResp`, `QuizResp`, `IndexStatusResp`) so FastAPI's generated OpenAPI schema (`/docs`) shows a typed envelope instead of free-form dict.
- **Why:** Demo presentation — the frontend gets correctly-typed TS bindings and the `/docs` page looks polished. No behavior change.
- **Status:** proposed — implemented on `cognee/iter`. Spec table columns still document raw JSON shapes, which remain accurate.

## Delta 4 — Pydantic bounds on `DiaryEntry` and `Material`

- **Section:** §2 data types.
- **Change:** Document: `DiaryEntry.text ∈ [1, 20000]`, `DiaryEntry.tags` length ≤ 32; `Material.text` ≥ 1, `Material.source ∈ [1, 512]`, `Material.course ∈ [1, 64]`.
- **Why:** Model-level bounds make these invariants observable both at HTTP boundaries (FastAPI → 422) and at direct-import boundaries (seed CLI, agent loop) without each caller re-validating. Matches spec §2 clause "extensions allowed; renames not".
- **Status:** proposed — implemented on `cognee/iter`. Spec text not yet amended.

## Delta 5 — `generate_quiz` accepts Pydantic-shaped `is_part_of`

- **Section:** §3 quiz retrieval — `source_ref` extraction.
- **Change:** The spec wrote `source_ref = chunks[0]["is_part_of"]["name"]`. Cognee's `ChunksRetriever` returns vector-store payloads; community vector engines sometimes return typed `DataPoint` objects or flatten `is_part_of` into a bare string. The service now tolerates all three (`dict["name"]`, `"bare-name"`, `obj.name`) and falls back to `None`.
- **Why:** Avoids silently losing source attribution when the vector engine changes; costs no perf; keeps the dict-form as the happy path.
- **Status:** proposed — implemented on `cognee/iter`. Spec clause not yet amended.

## Delta 6 — Quiz prompt quality

- **Section:** §3 quiz prompt.
- **Change:** The system prompt now enumerates rules: grounded-only, diverse question types (definitional/mechanism/application/comparison), no trivial restatements, 1–2 sentence answers, strict JSON shape.
- **Why:** Demo-visible quality. Demo criterion says 25% Quality + 25% Presentation — a quiz that varies question types beats five definitional clones. Low risk since the schema contract is unchanged.
- **Status:** proposed — implemented on `cognee/iter`. Spec clause not yet amended.

## Delta 3 — HTTP request bounds

- **Section:** §4 HTTP routes (implicit — spec gives only field types).
- **Change:** Document: `QuizReq.n ∈ [1, 20]`, `QuizReq.topic` length ≤ 200, `QueryReq.q` length ∈ [1, 2000].
- **Why:** Defense against accidental pathological payloads (e.g. `n=10000` thrashing the LLM). No legitimate use case exceeds these bounds.
- **Status:** proposed — implemented on `cognee/iter`. Spec table not yet amended.
