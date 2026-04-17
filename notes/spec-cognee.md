# Spec — Cognee Layer

Scope: the cognee service and its contracts with the rest of the backend. Everything else (agent loop, frontend, LLM provider config outside cognee) is out of scope.

Status: pinned — ready to implement.

---

## 1. Datasets

Two cognee datasets, kept disjoint:

| Dataset | What goes in | Typical query |
|---|---|---|
| `diary` | Chat-style journal entries, dated | "what study patterns worked this month?" |
| `materials` | Lecture notes, readings, textbook text | "explain backpropagation grounded in my notes" |

Rationale: pattern queries over diary shouldn't pull textbook prose and vice versa. Dataset isolation is enforced by passing `dataset_name=` on `add` and `datasets=[...]` on `cognify` / `search` — cognee v1 supports this natively.

---

## 2. Data types

```python
# backend/app/types.py
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class DiaryEntry(BaseModel):
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    tags: list[str] = []            # user/caller-supplied, e.g. ["ml", "pomodoro", "productive"]

class Material(BaseModel):
    text: str
    source: str                     # filename or URL
    course: str                     # e.g. "ML-L3"

class QuizItem(BaseModel):
    question: str
    answer: str
    topic: str
    source_ref: str | None = None   # filename of a retrieved chunk, when available
```

Frozen. Extensions allowed; renames are not.

**`tags` ownership:** caller-supplied, optional. The cognee service does not extract tags. (A future agent pass may enrich them; out of scope here.)
**`ts` ownership:** caller-supplied; server stamps `now()` if omitted.

---

## 3. Service interface (`backend/app/cognee_service.py`)

All functions are `async`. All raise `CogneeServiceError` on cognee-level failures.

```python
class CogneeServiceError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable

async def add_diary_entry(entry: DiaryEntry) -> None
async def add_material(material: Material) -> None
async def cognify_dataset(dataset: Literal["diary", "materials"]) -> None
async def query_diary(q: str) -> str
async def query_materials(q: str) -> str
async def generate_quiz(topic: str, n: int = 5) -> list[QuizItem]
async def index_status() -> dict[str, Literal["idle", "indexing"]]
async def reset() -> None              # debug only; calls cognee.prune.*
```

**Semantics:**
- `add_*` — fast (<100ms). No LLM call. Calls `cognee.add(data=..., dataset_name=...)`. Input is sanitized (strip `\x00`); empty input raises `ValueError`. No auto-trigger of cognify — callers (agent loop, seed CLI) decide when to index.
  - Diary serialization into cognee: `f"[{entry.ts.isoformat()}] {entry.text}" + (f" Topics: {', '.join(entry.tags)}." if entry.tags else "")`. ISO date becomes a temporal marker in the KG; tags read as a natural-language trailing sentence so the entity extractor treats them as topical context rather than a pseudo-entity.
  - Material serialization: raw `material.text`; `source` and `course` are prepended as a header line — `f"[source={material.source} course={material.course}]\n{material.text}"`.
- `cognify_dataset` — slow (seconds to minutes). Calls `cognee.cognify(datasets=[dataset])`. **Awaits** the per-dataset lock (§7). Idempotent: cognee re-indexes only what's new, so back-to-back calls are cheap no-ops on the second run.
- `query_*` — returns synthesized answer string. Uses `cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=q, datasets=[...])` and joins the result list into a single string.
- `generate_quiz` — two-step:
  1. `cognee.search(query_type=SearchType.CHUNKS, query_text=topic, datasets=["materials"], top_k=10)` to pull top-k chunks. Result is a `list[dict]` *or* a bare `dict` when there's a single hit — normalize: `if isinstance(r, dict): r = [r]`. If empty, raises `CogneeServiceError("no material found for topic")`.
  2. Direct LiteLLM call for structured JSON, with `response_format` passed via `extra_body` to dodge LiteLLM's known stripping bug on the OpenRouter route (BerriAI/litellm#10465):
     ```python
     await litellm.acompletion(
         model=settings.llm_model,                 # e.g. "openrouter/openai/gpt-4o-mini"
         api_key=settings.llm_api_key,             # OpenRouter key
         messages=[...],
         extra_body={
             "response_format": {
                 "type": "json_schema",
                 "json_schema": {
                     "name": "quiz",
                     "strict": True,
                     "schema": {
                         "type": "object",
                         "properties": {
                             "items": {
                                 "type": "array",
                                 "items": {
                                     "type": "object",
                                     "properties": {
                                         "question": {"type": "string"},
                                         "answer": {"type": "string"},
                                     },
                                     "required": ["question", "answer"],
                                 },
                             },
                         },
                         "required": ["items"],
                     },
                 },
             }
         },
     )
     ```
     Prompt instructs the model to produce `n` Q&A grounded in the retrieved chunks. Parse `response.choices[0].message.content` as JSON. Populate `QuizItem.topic = topic`; `source_ref = chunks[0]["is_part_of"]["name"]` when the top chunk has it, else `None`. On JSON parse failure: `CogneeServiceError("quiz generation returned invalid JSON", retryable=True)` — caller retries at most once.
  **Credential wiring:** `api_key` and `model` are passed explicitly from `settings`. The `openrouter/<model>` prefix tells LiteLLM to route via its built-in OpenRouter provider — no `api_base` argument needed.
- `index_status` — returns the current in-process `_state` map. Read-only.
- `reset` — calls `cognee.prune.prune_data()` then `cognee.prune.prune_system(metadata=True)`. Not exposed via HTTP.

**Errors:**
- `CogneeServiceError` wraps cognee exceptions; carries `.retryable: bool`. Timeouts and rate-limit errors map to `retryable=True`; schema/API errors to `retryable=False`.
- `ValueError` on empty input.

---

## 4. HTTP routes (owned by cognee layer)

Mounted under a single `APIRouter` in `backend/app/routes_cognee.py`.

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/diary` | `DiaryEntry` | `{status: "queued"}` |
| POST | `/materials` | `Material` | `{status: "queued"}` |
| POST | `/cognify` | `{dataset: "diary"\|"materials"}` | `{status: "indexing"}` |
| POST | `/diary/query` | `{q: str}` | `{answer: str}` |
| POST | `/materials/query` | `{q: str}` | `{answer: str}` |
| POST | `/quiz` | `{topic: str, n?: int}` | `{items: QuizItem[]}` |
| GET | `/index-status` | — | `{diary: "idle"\|"indexing", materials: "idle"\|"indexing"}` |

**`/cognify` handler:** `asyncio.create_task(cognify_dataset(req.dataset))` and return immediately. Never blocks the request. Callers poll `/index-status` for completion. The service function itself is what awaits the lock — overlapping `/cognify` calls pile up as tasks, each blocked on the dataset lock, and run serially. Waste is bounded by cognify's idempotency.

**HTTP error mapping:**
- `ValueError` → 400
- `CogneeServiceError` → 503 if `retryable`, else 500
- Unknown → 500

**No `/reset` route.** Use `seed.py reset` instead.

---

## 5. Seeding

CLI script at `backend/scripts/seed.py`:

```
uv run python -m scripts.seed ingest <dir>    # add files, no cognify
uv run python -m scripts.seed index            # cognify both datasets
uv run python -m scripts.seed reset            # wipe
```

- Manifest at `seed/.state.json` tracks SHA256 of ingested chunks → re-runnable without dupes.
- Dataset is chosen by subfolder: `<dir>/diary/*.md` → `diary` dataset, `<dir>/materials/*.md` → `materials` dataset.
- Loader registry keyed by suffix. Initial support: `.txt`, `.md`. PDFs must be pre-extracted.
- `ingest`, `index`, and `reset` bypass the HTTP layer and call `cognee_service` directly. `index` awaits `cognify_dataset` so the CLI blocks until indexing completes — no HTTP polling needed.

---

## 6. Config (env vars)

Single credential (OpenRouter) for both LLM and embeddings. Env var names match cognee's canonical names so both cognee and our own `pydantic-settings` load from the same `.env`.

```
# --- LLM via OpenRouter ---
LLM_PROVIDER=custom
LLM_MODEL=openrouter/openai/gpt-4o-mini
LLM_ENDPOINT=https://openrouter.ai/api/v1
LLM_API_KEY=${OPENROUTER_API_KEY}
LLM_INSTRUCTOR_MODE=tool_call    # avoids the response_format stripping bug on OpenRouter
LLM_MAX_COMPLETION_TOKENS=4096
LLM_TEMPERATURE=0.0

# --- Embeddings via OpenRouter (raw openai SDK, not LiteLLM) ---
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_ENDPOINT=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=${OPENROUTER_API_KEY}
EMBEDDING_DIMENSIONS=1536

# --- Runtime ---
DATA_ROOT_DIRECTORY=./.cognee_system
TELEMETRY_DISABLED=true
```

Why `LLM_PROVIDER=custom`: cognee's `custom` adapter defaults to `json_mode` and is explicitly documented for OpenAI-compatible gateways like OpenRouter. `openai` provider hardcodes `json_schema_mode` which has patchier OpenRouter support per model.

Why `LLM_INSTRUCTOR_MODE=tool_call`: cognee uses `instructor` on top of LiteLLM for structured extraction during cognify. LiteLLM has a known bug (BerriAI/litellm#10465) stripping `response_format` on some OpenRouter routes. `tool_call` mode uses function-calling, which is widely supported and bypasses that path.

Why `EMBEDDING_PROVIDER=openai_compatible`: cognee's `OpenAICompatibleEmbeddingEngine` uses the raw `openai` SDK against the configured `base_url`, deliberately avoiding LiteLLM's `/v1/embeddings` compatibility issues.

Read via `pydantic-settings` (class `Settings`). Our `Settings` exposes only the fields we use directly: `llm_api_key`, `llm_model`, `data_root_directory`. Everything else is read by cognee straight from `os.environ`. Missing required keys = startup failure with clear message.

**Fallback config** (if OpenRouter embeddings have quality issues on the corpus): swap `EMBEDDING_PROVIDER=openai`, point `EMBEDDING_API_KEY` at a real OpenAI key, drop `EMBEDDING_ENDPOINT`.

---

## 7. Concurrency & indexing state

In-process only; no external coordination needed for the hackathon.

```python
# module-level in cognee_service.py
_state: dict[str, Literal["idle", "indexing"]] = {"diary": "idle", "materials": "idle"}
_locks: dict[str, asyncio.Lock] = {"diary": asyncio.Lock(), "materials": asyncio.Lock()}

async def cognify_dataset(dataset: str) -> None:
    async with _locks[dataset]:
        _state[dataset] = "indexing"
        try:
            await cognee.cognify(datasets=[dataset])
        finally:
            _state[dataset] = "idle"
```

Guarantees:
- At most one cognify per dataset at any time → avoids Kuzu/SQLite lock weirdness flagged in the cognee dossier.
- Overlapping calls queue on the lock and run in order. Cognify is idempotent, so back-to-back runs are cheap.
- `/cognify` route wraps the call in `asyncio.create_task` and returns immediately; seed CLI awaits it directly and blocks until done.
- Process restart loses `_state`, which is fine: the next cognify call repopulates it.

No timer, no pending flag, no auto-trigger on `/diary` or `/materials` posts. The agent loop owns pacing decisions.

---

## 8. Out of scope

- Multi-user / auth / per-user datasets.
- Live PDF / docx / slide parsing at runtime (text only; pre-extract offline).
- Streaming responses.
- Persistence migrations between cognee versions. **Pin `cognee==1.0.0`** in `pyproject.toml`.
- v2 cognee API (`remember` / `recall`). Use v1 only.
- Observability beyond a single `logger.info` per operation.
- Caching of query results.
- Cross-process indexing coordination (single-process assumption).
- Automatic tag extraction for diary entries (agent loop's job, not ours).
- Auto-triggering cognify from add endpoints (agent loop's job).

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| `cognify` slow / blocks event loop | Route wraps call in `asyncio.create_task`; seed narrowly before demo |
| LLM cost on `cognify` | Use `gpt-4o-mini`; seed narrowly; agent loop cognifies on a sane cadence |
| Mid-hackathon cognee dev release breaks API | Pin `cognee==1.0.0`; commit `uv.lock` |
| OpenRouter embedding quality varies per model | Default to `openai/text-embedding-3-small`; fallback config (§6) swaps to OpenAI direct |
| LiteLLM strips `response_format` on OpenRouter routes | Pass via `extra_body` in our quiz call; cognee uses `LLM_INSTRUCTOR_MODE=tool_call` to avoid the path |
| Seed data loss between demos | Commit `.cognee_system/` OR run seed CLI in demo setup step |
| Null bytes in ingested text crash cognee | Strip `\x00` in `add_*` boundary |
| Oversized single paragraph errors chunker | Acceptable for MVP; hand-curate seed data |
| `SearchType.GRAPH_COMPLETION` returns no source lineage | Accept — quiz uses `SearchType.CHUNKS` instead so `source_ref` is recoverable |
| LLM returns malformed JSON in `generate_quiz` | JSON mode (`response_format`) makes this rare; wrap in try/except, raise `CogneeServiceError(retryable=True)`; caller retries once |
| Agent loop forgets to call `/cognify` | Contract is documented here; responsibility sits with agent layer |
| litellm reads wrong env var | Pass `api_key` and `model` explicitly, don't rely on inheritance |
| cognee v1 search result shape inconsistency (single-hit unwrap) | Normalize: `if isinstance(r, dict): r = [r]` before iterating |

---

## 10. Acceptance

Spec is done when:
1. `cognee_service.py` implements all functions in §3 against real cognee v1 calls (no fixtures).
2. All routes in §4 return valid schemas against a seeded dataset.
3. `seed.py ingest examples/` + `seed.py index` produces queryable state.
4. `POST /quiz {topic: "transformers"}` returns ≥1 `QuizItem` grounded in a seeded file, with `source_ref` populated when the top chunk has a filename.
5. Two overlapping `POST /cognify` calls on the same dataset serialize via the lock (no concurrent cognify observed in logs).

---

## 11. Implementation order

For whoever picks this up:

1. `pyproject.toml` — pin `cognee==1.0.0`, add `pydantic-settings>=2`, `litellm>=1.50`. Run `uv sync`.
2. `.env` from `.env.example` with the OpenRouter key plugged in.
3. `types.py` — pydantic models (§2).
4. `config.py` — `Settings(BaseSettings)` exposing only `llm_api_key`, `llm_model`, `data_root_directory`. Cognee reads the rest directly from env.
5. `cognee_service.py` — `CogneeServiceError`, `_state`, `_locks`, then `add_*`, `cognify_dataset` (with lock), `query_*`, `index_status`, `reset`. Stub `generate_quiz` last.
6. `generate_quiz` — CHUNKS retrieval + litellm call with `extra_body` JSON schema (§3); extract `source_ref` from `chunks[0]["is_part_of"]["name"]`.
7. `routes_cognee.py` — thin FastAPI router wiring the service to §4; `/cognify` uses `asyncio.create_task`.
8. `main.py` — mount router; add `lifespan` that calls `cognee.run_startup_migrations()` once.
9. `scripts/seed.py` — manifest-based ingest/index/reset; `index` awaits `cognify_dataset` directly.
10. Drop a handful of `.md` files into `seed/materials/` and `seed/diary/`; run acceptance checks.
