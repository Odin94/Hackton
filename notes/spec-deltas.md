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
