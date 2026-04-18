# Study Diary — Backend

FastAPI service providing the **cognee memory layer** for the Hackton study-diary agent. Owns the two cognee datasets (`diary`, `materials`), the HTTP contract for adding/indexing/querying them, and the grounded-quiz generator.

Spec of record: [`../notes/spec-cognee.md`](../notes/spec-cognee.md). Proposed deltas: [`../notes/spec-deltas.md`](../notes/spec-deltas.md).

---

## Setup

Requires Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev
cp .env.example .env   # then edit — see "Env" below
```

## Run

```bash
uv run fastapi dev main.py        # dev server with reload
uv run fastapi run main.py        # prod mode
```

OpenAPI docs at `http://localhost:8000/docs`.

## Seed

The seed CLI ingests local files into the two cognee datasets and runs cognify. Re-runnable — files with unchanged SHA256 are skipped via `seed/.state.json`.

```bash
uv run python -m scripts.seed ingest seed/    # add files, no cognify
uv run python -m scripts.seed index           # cognify both datasets (slow: LLM calls)
uv run python -m scripts.seed reset           # wipe cognee + manifest
```

Layout expected under the ingest root:

- `seed/diary/YYYY-MM-DD-anything.md` — dated diary entries. The filename date becomes `DiaryEntry.ts`; missing/invalid dates fall back to `now()`.
- `seed/materials/<course>-<rest>.md` — lecture materials. The leading `<course>-<rest>` is uppercased into `Material.course` (e.g. `ml-l3-transformers.md` → `ML-L3`).

Supported extensions: `.md`, `.txt`. Per-file errors are logged and do not abort the run; a non-zero exit signals any failures.

## HTTP routes

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/diary` | `DiaryEntry` | `{status: "queued"}` |
| POST | `/materials` | `Material` | `{status: "queued"}` |
| POST | `/cognify` | `{dataset: "diary"\|"materials"}` | `{status: "indexing"}` (returns immediately; indexing runs in a background task) |
| POST | `/diary/query` | `{q: str}` | `{answer: str}` (GRAPH_COMPLETION search) |
| POST | `/materials/query` | `{q: str}` | `{answer: str}` |
| POST | `/quiz` | `{topic: str, n?: int}` | `{items: QuizItem[]}` (CHUNKS retrieval + LiteLLM JSON-schema call) |
| GET | `/index-status` | — | `{diary: "idle"\|"indexing", materials: "idle"\|"indexing"}` |
| GET | `/health` | — | `{status: "ok"}` |

Request bounds: `QuizReq.n ∈ [1, 20]`, `QuizReq.topic ≤ 200`, `QueryReq.q ∈ [1, 2000]`. Out-of-bounds → HTTP 422.

Error mapping: `ValueError` → 400, `CogneeServiceError(retryable=False)` → 500, `CogneeServiceError(retryable=True)` → 503, unknown → 500.

## Env

See `.env.example` for the full set. Relevant pieces:

```
LLM_PROVIDER=custom
LLM_MODEL=openrouter/openai/gpt-4o-mini
LLM_ENDPOINT=https://openrouter.ai/api/v1
LLM_API_KEY=<OpenRouter key>
LLM_INSTRUCTOR_MODE=tool_call

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=<OpenRouter key>
EMBEDDING_DIMENSIONS=1536

DATA_ROOT_DIRECTORY=./.cognee_system
TELEMETRY_DISABLED=true
```

`LLM_API_KEY` is the only required var for our own `Settings` class; the rest are read directly from the env by cognee. Missing `LLM_API_KEY` → startup failure.

## Tests

Full suite is mock-based — no network, no LLM calls:

```bash
uv run pytest                       # all tests
uv run pytest -k quiz               # filter by keyword
uv run pytest tests/test_routes.py  # specific file
```

The cognee + litellm modules are monkeypatched at the module boundary in `tests/conftest.py`, so tests run in a fraction of a second even though the service imports both live.

## Troubleshooting

- **`ValidationError: llm_api_key`** at startup — `.env` is missing or `LLM_API_KEY` isn't set. Cognee also needs the variables above; the service only validates its own required subset.
- **`CogneeServiceError: LLM call exceeded 30s timeout`** from `/quiz` — OpenRouter is slow; `_LLM_TIMEOUT_SECONDS` in `app/cognee_service.py` is the knob. The route surfaces this as 503 so clients can retry.
- **Two `/cognify` calls return `{"status": "indexing"}` instantly, but the second one actually waits** — by design. Per-dataset `asyncio.Lock` serializes them; cognify is idempotent, so the overlap is cheap.
- **`/quiz` returns `no material found for topic: X`** — either the `materials` dataset is empty or `/cognify` on materials hasn't finished. Check `GET /index-status`.
- **Cognee v1 returns a single dict instead of a list** — service normalizes single-hit dicts into `[dict]` before iterating (documented in spec §9).
