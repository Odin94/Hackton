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

## Delta 3 — HTTP request bounds

- **Section:** §4 HTTP routes (implicit — spec gives only field types).
- **Change:** Document: `QuizReq.n ∈ [1, 20]`, `QuizReq.topic` length ≤ 200, `QueryReq.q` length ∈ [1, 2000].
- **Why:** Defense against accidental pathological payloads (e.g. `n=10000` thrashing the LLM). No legitimate use case exceeds these bounds.
- **Status:** proposed — implemented on `cognee/iter`. Spec table not yet amended.
