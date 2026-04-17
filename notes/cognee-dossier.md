# Cognee Dossier

*Compiled 2026-04-17 for a ~2-4 hour hackathon spike. Sources: github.com/topoteretes/cognee, docs.cognee.ai, PyPI, GitHub issues/releases, DeepWiki, community articles.*

---

## 1. One-line summary

**Cognee is an open-source Python "knowledge engine" that turns unstructured text/files into a searchable knowledge graph + vector store combo, giving AI agents a persistent memory layer you can query with one function call.**

Marketing tagline from the repo: *"Knowledge Engine for AI Agent Memory in 6 lines of code."* Python 3.10-3.13. Apache-2.0.

---

## 2. Core concept

### What problem it solves
Standard RAG = "dump chunks into a vector DB, retrieve top-k by cosine similarity." That breaks down when:
- You need multi-hop reasoning (*"Which of Alice's co-authors work at companies founded after 2020?"*)
- You want entities deduplicated across documents
- You want an audit trail / lineage from answer back to source
- You want the agent to *learn* from new documents without re-indexing everything

Cognee's pitch is that it sits between "raw LLM" and "I wrote a full KG pipeline myself." It auto-extracts entities and relationships from whatever text you throw at it, stores them in a graph DB, *and* embeds chunks into a vector DB, *and* tracks source lineage in a relational DB — all behind one `.add()` → `.cognify()` → `.search()` flow.

### What it actually does
Three coordinated stores working together (the docs call this "ECL" — Extract, Cognify, Load, their take on ETL):
1. **Relational store** → documents, chunks, data lineage, job state.
2. **Vector store** → embeddings for semantic matching.
3. **Graph store** → entities (nodes) and relationships (edges) extracted by an LLM during `cognify`.

A query can be routed to any of these or (by default) fuse all three.

### How it differs from alternatives

| Thing | Cognee | Plain vector RAG | LangChain `ConversationBufferMemory` etc. |
|---|---|---|---|
| Stores relationships between entities | Yes, real graph DB | No | No |
| Auto-extracts entities from docs | Yes (via LLM during `cognify`) | No | No |
| Multi-hop / graph-traversal search | Yes (`GRAPH_COMPLETION`) | No | No |
| Just a dict/list of past turns | No | No | Yes |
| Zero-config local default | Yes (SQLite + LanceDB + Kuzu) | Depends | Yes |
| Persistent across sessions | Yes | Yes (if you wire it up) | Usually no |
| Cost profile | Pays LLM tokens during ingest (entity extraction) | Pays embeddings only during ingest | Near-free |

Bottom line: Cognee is heavier at ingest time (it calls the LLM to extract a graph) but can answer questions plain RAG can't. LangChain memory is a different thing entirely — short-term chat state vs. long-term document knowledge.

---

## 3. Architecture

### Default stack (zero config)
- **LLM:** `openai/gpt-4o-mini`
- **Embeddings:** `openai/text-embedding-3-large` (3072 dims)
- **Relational DB:** SQLite (on-disk file)
- **Vector DB:** LanceDB (on-disk, no server)
- **Graph DB:** Kuzu (embedded, no server)
- **Temperature:** 0.0
- **Telemetry:** ON by default (disable with `TELEMETRY_DISABLED=true`)

This means `pip install cognee` + `LLM_API_KEY` = runnable. No Docker, no Neo4j, no Postgres needed for a hackathon.

### Supported swap-ins
- **Graph stores:** Kuzu (default), Kuzu-remote, Neo4j, Amazon Neptune, Neptune Analytics, Memgraph, FalkorDB
- **Vector stores:** LanceDB (default), PGVector, Qdrant, Redis, ChromaDB, FalkorDB, Neptune Analytics
- **Relational:** SQLite (default) or Postgres
- **LLM providers:** OpenAI (default), Azure OpenAI, Anthropic, Google Gemini, Ollama (local), vLLM / custom OpenAI-compatible endpoints

### Data flow through an ingest
```
raw input (text/pdf/url/s3 path)
     │
     ▼  cognee.add()
[relational DB]  ← stores Document, Chunk rows, tracks lineage
     │
     ▼  cognee.cognify()
     ├─► chunker splits text into token-bounded chunks
     ├─► embedder → [vector DB]
     ├─► LLM entity/relationship extractor → [graph DB] (nodes + edges)
     └─► summarizer → hierarchical summary nodes (used by SearchType.SUMMARIES)
```

Modules live under `cognee/`:
`api/` (HTTP + versioned routers), `pipelines/`, `memify_pipelines/`, `tasks/`, `modules/`, `infrastructure/`, `eval_framework/`, `cli/`, `alembic/` (DB migrations).

Two API generations ship side by side (see §5):
- **v1:** `add` / `cognify` / `search` — granular, what most code and tutorials use.
- **v2:** `remember` / `recall` / `forget` / `improve` — higher-level wrapper introduced around v1.0.0 (April 2026).

---

## 4. Install & quickstart

### Install
```bash
# Recommended (fast, uv-based)
uv pip install cognee

# Plain pip also fine
pip install cognee
```

Python 3.10 – 3.13. The package pulls in a lot (LiteLLM, LanceDB, Kuzu, SQLAlchemy, Alembic, aiohttp, OpenTelemetry, Pydantic v2, etc.) — expect ~30s-1min cold install.

### Minimum env vars
For the default (OpenAI) stack, you only need:
```bash
export LLM_API_KEY="sk-..."          # used for both LLM and embeddings by default
```
Or in Python:
```python
import os
os.environ["LLM_API_KEY"] = "sk-..."
```

A `.env` file at the project root is auto-loaded (`python-dotenv` is baked in).

**Gotcha:** if you set `LLM_PROVIDER=anthropic` but don't set `EMBEDDING_PROVIDER`, embeddings still default to OpenAI and will 401 without an OpenAI key. Set both or set `EMBEDDING_API_KEY` separately.

Other useful env vars (all documented; prefixes are *not* `COGNEE_`):
```bash
LLM_PROVIDER=openai            # openai | anthropic | gemini | ollama | azure | custom
LLM_MODEL=gpt-4o-mini
LLM_ENDPOINT=                  # for vLLM/Ollama/Azure
LLM_TEMPERATURE=0.0
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072
EMBEDDING_API_KEY=
VECTOR_DB_PROVIDER=lancedb     # lancedb | qdrant | pgvector | chromadb | redis | ...
GRAPH_DATABASE_PROVIDER=kuzu   # kuzu | neo4j | memgraph | neptune | ...
GRAPH_DATABASE_URL=
GRAPH_DATABASE_USERNAME=
GRAPH_DATABASE_PASSWORD=
DB_PROVIDER=sqlite             # sqlite | postgres
DATA_ROOT_DIRECTORY=           # where cognee stores its files; defaults to cwd/.cognee_system
TELEMETRY_DISABLED=true
LOG_LEVEL=INFO
```

### Copy-paste-able quickstart (v1 API — the one you'll see in 99% of examples)

```python
# quickstart.py
import os
import asyncio

os.environ["LLM_API_KEY"] = "sk-..."  # or set in .env

import cognee
from cognee.api.v1.search import SearchType


async def main():
    # 1. (optional) wipe previous state — useful during dev
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)

    # 2. Ingest. Accepts raw strings, file paths, URLs, s3://, file:// ...
    await cognee.add(
        data="Natural language processing (NLP) is a subfield of computer "
             "science and information retrieval.",
        dataset_name="nlp_intro",
    )

    # 3. Build the knowledge graph + embeddings. This is the expensive step.
    await cognee.cognify(datasets="nlp_intro")

    # 4. Ask questions.
    answer = await cognee.search(
        query_type=SearchType.GRAPH_COMPLETION,
        query_text="What is NLP related to?",
        datasets=["nlp_intro"],
    )
    for r in answer:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
```

### v2 API (shorter, released with v1.0.0)

```python
import cognee, asyncio

async def main():
    await cognee.remember("Cognee turns documents into AI memory.")
    # optional per-session isolation:
    await cognee.remember("User prefers detailed explanations.", session_id="chat_1")

    results = await cognee.recall(query_text="What does Cognee do?")
    for r in results:
        print(r)

    await cognee.forget(dataset="main_dataset")

asyncio.run(main())
```

### CLI (handy for sanity-checking)
```bash
cognee-cli remember "Cognee turns documents into AI memory."
cognee-cli recall   "What does Cognee do?"
cognee-cli forget --all
cognee-cli -ui                 # opens a local web UI for inspecting the graph
```

---

## 5. Python API surface

Exports from `cognee/__init__.py` (verified against the repo `main` branch):

### v1 — pipeline-level (granular)
| Name | Purpose |
|---|---|
| `cognee.add(data, dataset_name=..., incremental_loading=True, data_per_batch=20)` | Ingest raw text, file path, URL, S3, or file-like object into a named dataset. |
| `cognee.cognify(datasets=..., chunk_size=..., chunks_per_batch=100, run_in_background=False, temporal_cognify=False)` | Run the chunk → embed → entity-extract → graph-build pipeline on previously-added data. Expensive: does LLM calls. |
| `cognee.search(query_type, query_text, datasets=None, save_interaction=False, last_k=None)` | Query the KG. See `SearchType` below. |
| `cognee.delete(...)` | Delete specific documents/datasets. |
| `cognee.update(...)` | Update existing records. |
| `cognee.prune.prune_data()` / `cognee.prune.prune_system(metadata=True)` | Nuke data / nuke system state. Useful during dev. |
| `cognee.datasets` | Dataset management helpers. |
| `cognee.config` | Runtime config setters (`cognee.config.set_llm_provider(...)` etc.) |
| `cognee.visualize_graph()` / `cognee.start_visualization_server()` / `cognee.start_ui()` | Spin up the graph visualizer. |
| `cognee.session` | Session-scoped memory helpers. |

### v2 — memory-oriented (higher level, new in v1.0.0)
| Name | Purpose |
|---|---|
| `cognee.remember(text_or_data, session_id=None, ...)` | One-shot ingest + cognify. If `session_id` given, stored in session memory that syncs to the graph in the background. |
| `cognee.recall(query_text, session_id=None, ...)` | Auto-routes to the best `SearchType` for the query. Returns a list of results (`RememberResult` objects). |
| `cognee.forget(dataset=..., session_id=..., all=False)` | Delete. |
| `cognee.improve(...)` | Enrich / re-run extraction on existing data. |
| `cognee.serve(...)` / `cognee.disconnect()` | Stand up / tear down the cognee HTTP service programmatically. |
| `cognee.visualize(...)` | Visualize the graph. |

### Also exported
- `SearchType` (enum), `RememberResult` (dataclass-ish result type)
- `memify`, `run_custom_pipeline`, `pipelines`, `Drop`
- `cognee_network_visualization`
- `agent_memory` (module for agent-specific memory ops)
- Tracing: `enable_tracing`, `disable_tracing`, `get_last_trace`, `get_all_traces`, `clear_traces`
- `run_startup_migrations` (run Alembic migrations explicitly)
- `__version__`

### `SearchType` values (for `cognee.search`)
The enum has 12 members. The ones you'll actually use:

| `SearchType.*` | What it does | Cost/latency |
|---|---|---|
| `GRAPH_COMPLETION` *(default)* | Vector search → graph traversal → LLM synthesizes an answer. | Highest |
| `RAG_COMPLETION` | Classic RAG: vector search → LLM. Ignores graph edges. | Medium |
| `CHUNKS` | Pure vector similarity, returns raw chunks. No LLM at query time. | Lowest |
| `SUMMARIES` | Returns pre-built hierarchical summaries (made at `cognify` time). | Low |
| `INSIGHTS` | Returns entities + their relationships, structured. | Low-medium |
| `CODE` | Code-aware variant (functions, classes, etc). | Medium |
| `FEELING_LUCKY` | LLM picks the best SearchType for you. | +1 LLM call |
| `GRAPH_SUMMARY_COMPLETION` | `GRAPH_COMPLETION` + an intermediate summarize step. | Higher |
| `GRAPH_COMPLETION_COT` | Chain-of-thought iterative refinement. | Much higher |
| `GRAPH_COMPLETION_CONTEXT_EXTENSION` | Iteratively pulls related graph triplets. | Much higher |
| `TEMPORAL` | Time-aware filtering ("what happened in 2023"). Requires `temporal_cognify=True` at ingest. | Medium |
| `FEEDBACK` | Not a search — records user feedback on a prior answer. | Tiny |

### Async vs sync
**Everything public is async.** Call with `await` inside an `async def`, or drive with `asyncio.run(main())`. The README states "All functions are async – use `await` or `asyncio.run()`." There's no sync wrapper — you have to be in an event loop.

---

## 6. Storage backends

### Zero-config behavior
Yes, it runs with zero config. On first `cognee.add(...)` it will:
- Create `<DATA_ROOT_DIRECTORY>/.cognee_system/` (or a similar dir in cwd)
- Initialize a SQLite file for relational state
- Initialize LanceDB files for vectors
- Initialize a Kuzu file for the graph
- Run Alembic migrations automatically (`run_startup_migrations`)

All three are embedded — no services to start, no Docker, no ports to expose. Perfect for hackathons.

### Production swap-ins
When you outgrow the defaults:
- **Graph:** Neo4j is the most battle-tested; Memgraph and FalkorDB are faster alternatives; Neptune / Neptune Analytics if you're in AWS land.
- **Vector:** PGVector (if you already use Postgres), Qdrant (self-hosted or cloud), Redis (if you already have it), ChromaDB (lightweight).
- **Relational:** Postgres via `DB_PROVIDER=postgres`.

You set them via env vars (§4) or `cognee.config` helpers; no code changes in your agent logic.

---

## 7. LLM provider support

Officially supported (listed in the configuration docs):
- **OpenAI** — default, easiest.
- **Azure OpenAI** — set `LLM_PROVIDER=azure`, `LLM_ENDPOINT=...`, `LLM_API_KEY=...`.
- **Anthropic** — `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-sonnet-4-...`. **Still needs an embedding provider** — Anthropic has no embeddings. Set `EMBEDDING_PROVIDER=openai` (+ key) or use a local embedder via Ollama.
- **Google Gemini**
- **Ollama** — for local models. Set `LLM_PROVIDER=ollama`, `LLM_ENDPOINT=http://localhost:11434`, `LLM_MODEL=llama3.1:8b`, and a matching embedding model. Ollama must be running separately.
- **vLLM / custom OpenAI-compatible endpoints** — `LLM_PROVIDER=custom` and point `LLM_ENDPOINT` at your server.

Under the hood it uses **LiteLLM**, so any model LiteLLM supports should work. One known issue: LiteLLM's `model_cost` lookup can override your token limit settings (open GitHub issue).

**Key config pattern:**
```python
import cognee
cognee.config.set_llm_provider("ollama")
cognee.config.set_llm_model("llama3.1:8b")
cognee.config.set_llm_endpoint("http://localhost:11434")
cognee.config.set_embedding_provider("ollama")
cognee.config.set_embedding_model("nomic-embed-text")
cognee.config.set_embedding_endpoint("http://localhost:11434")
```
(Function names are from the config module pattern; for hackathon work the env-var route is safer.)

---

## 8. FastAPI integration notes

Cognee *is itself* a FastAPI app internally (`cognee.api.v1.*` routers for `add`, `cognify`, `memify`, `search`, `delete`, `users`, `datasets`, `responses`, `visualize`, `settings`, `sync`, `update`, `checks`). You can either:
- **Run cognee's bundled API** — `cognee.serve(...)` / `cognee-cli -ui` — and call it over HTTP from your own backend. Simplest integration.
- **Embed it in-process** — import `cognee` and call its async functions from your own FastAPI routes. This is what most hackathon teams want.

### Embedding pattern

Because every cognee call is async, you should use `async def` endpoints and `await` directly — no `run_in_threadpool`.

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import cognee
from cognee.api.v1.search import SearchType


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: migrations run automatically on first import, but you can force it
    await cognee.run_startup_migrations()   # safe no-op if already migrated
    # Optional: prime a dataset
    yield
    # Shutdown: cognee has no explicit close; default embedded DBs flush on GC.
    # If you called cognee.serve(...), call cognee.disconnect() here.


app = FastAPI(lifespan=lifespan)


class IngestReq(BaseModel):
    text: str
    dataset: str = "default"


@app.post("/ingest")
async def ingest(req: IngestReq):
    await cognee.add(data=req.text, dataset_name=req.dataset)
    # cognify is expensive — run in background for a responsive UI
    await cognee.cognify(datasets=req.dataset, run_in_background=True)
    return {"ok": True}


class AskReq(BaseModel):
    question: str
    dataset: str = "default"


@app.post("/ask")
async def ask(req: AskReq):
    try:
        results = await cognee.search(
            query_type=SearchType.GRAPH_COMPLETION,
            query_text=req.question,
            datasets=[req.dataset],
        )
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"results": [str(r) for r in results]}
```

### Things to know
- **`cognify` is slow** (seconds to minutes — it's doing LLM extraction). Don't block a request on it. Either use `run_in_background=True`, or kick it to a Celery/ARQ worker, or (hackathon-grade) `asyncio.create_task(cognee.cognify(...))` and return 202.
- **Only one event loop.** cognee expects to run inside asyncio. FastAPI gives you that. Don't mix with `asyncio.run()` inside a handler — you'll double-nest loops. Just `await`.
- **State is global.** cognee stores its handles in module-level singletons. Don't assume per-request isolation — use `dataset_name` and/or v2 `session_id` to scope data per user.
- **Migrations** run on first import — if you import cognee at module load, expect a ~1s startup cost.
- **File paths:** if you pass `cognee.add(data="./notes.pdf")`, the path is resolved relative to the server's CWD, not the request. Use absolute paths or upload to temp.
- **Concurrent cognify on the same dataset** can cause weird DB-lock states with SQLite/Kuzu. Serialize writes per dataset (an `asyncio.Lock` per dataset key is enough for a hackathon).

---

## 9. Gotchas / known issues

Curated from GitHub issues and release notes as of April 2026. Things that will bite you in a 4-hour sprint:

1. **Ingest cost is non-trivial.** `cognify` calls the LLM many times per document (entity extraction, summary generation). A handful of pages can be a few cents to a few dollars on gpt-4o. Test with a *tiny* doc first. Use `gpt-4o-mini` (the default) — don't switch to `gpt-4o` unless you mean to.
2. **`cognify` is slow.** N+1 query pattern in `/api/v1/cognify` is a known open issue causing slow DB queries. On a hackathon laptop with a 5-page PDF, budget 30s-2min.
3. **API split is confusing.** v1 (`add`/`cognify`/`search`) and v2 (`remember`/`recall`) coexist. Docs and blog posts mix them. Stick to one — **v1 has more examples and tutorials**, v2 is shorter but newer (v1.0.0+, April 2026). If an LLM-generated snippet mixes them, assume it's wrong and check.
4. **Default models silently switch.** v1.0.0 enabled caching by default — release notes call it a breaking change. If you pin an old example that turns caching off, it may now behave differently.
5. **Embedding provider trap.** Setting `LLM_PROVIDER=anthropic` does *not* set embeddings. Anthropic has no embeddings API. You'll get an OpenAI 401 if you forgot `EMBEDDING_API_KEY`.
6. **Null bytes crash ingestion.** Documents containing `\x00` can crash `add_data_points`. Strip them if you're ingesting PDFs or scraped content.
7. **Text chunking fails on oversized paragraphs.** If a single paragraph is longer than `chunk_size`, it can error. Pre-split huge single-paragraph dumps.
8. **Relational DB grows unbounded.** `cognee_db` (SQLite) has no eviction on results/queries tables — open issue. Fine for a hackathon, not for long-running deployments.
9. **User-defined DataPoint IDs get dropped** during some model operations (open issue). Don't assume your own IDs survive.
10. **HTTP/2 stream stalls with Anthropic** — 60s timeouts reported. If using Claude, set a shorter per-request timeout and retry.
11. **Web scraping isn't built in.** `cognee.add("https://...")` fetches only simple URLs; dynamic JS pages aren't rendered. Pre-scrape with your own tool.
12. **Telemetry is on by default.** Set `TELEMETRY_DISABLED=true` if you need to keep data local.
13. **Async everywhere.** If you drop cognee into a Flask or Django-sync handler, you'll need to shim with `asyncio.run` or `anyio` — not worth it for a hackathon. Just use FastAPI.
14. **Windows:** no confirmed Windows-specific bugs in recent issues, but some dependencies (LanceDB, Kuzu) historically had wheels-on-Windows gaps. WSL is the safer bet.
15. **Dev-branch releases are common.** You'll see `1.0.1.dev0`..`1.0.1.dev3` between stable 1.0.0 and 1.0.1. If you `pip install cognee` plain, pip may pick a stable and miss a dev fix. Pin explicitly if a fix matters.

---

## 10. Current version & recency

As of **2026-04-17**:
- **Latest pre-release:** `v1.0.1.dev3` (2026-04-16)
- **Latest stable:** `v1.0.0` (2026-04-11) — first stable release of the project
- **Immediate dev cadence:** `dev0` (Apr 14) → `dev1` (Apr 15) → `dev2` (Apr 16) → `dev3` (Apr 16). Multiple commits per day.
- **Repo:** 16.2k stars, 1.7k forks, 6,746 commits. 86.5% Python, 13.1% TypeScript (web UI).
- **Maintenance:** very active. Multiple releases per week, issues being triaged, a clear roadmap in the release notes.

For a hackathon today: **use `v1.0.0` stable** unless you specifically need a dev fix (bulk CSV/JSON ingestion landed in `1.0.1.dev3`, per-user ontology storage in `1.0.1.dev1`).

```bash
uv pip install cognee==1.0.0
```

---

## 11. License

**Apache-2.0.** No copyleft concerns. Commercial use, modification, and distribution allowed; attribution + NOTICE file preserved.

---

## 12. Links

### Primary
- **GitHub:** https://github.com/topoteretes/cognee
- **Docs:** https://docs.cognee.ai
- **Quickstart:** https://docs.cognee.ai/getting-started/quickstart
- **Core concepts:** https://docs.cognee.ai/core-concepts
- **Configuration:** https://docs.cognee.ai/how-to-guides/configuration
- **PyPI:** https://pypi.org/project/cognee/
- **Releases:** https://github.com/topoteretes/cognee/releases
- **Issues:** https://github.com/topoteretes/cognee/issues

### Worth reading for a hackathon
- **Starter repo (examples):** https://github.com/topoteretes/cognee-starter — default pipeline, low-level pipeline (JSON), and custom Pydantic-model pipeline. Copy from here first.
- **DeepWiki quick-start:** https://deepwiki.com/topoteretes/cognee/1.2-quick-start-guide — clearer API reference than the official docs in places.
- **SearchType deep dive:** https://dev.to/chinmay_bhosale_9ceed796b/search-types-in-cognee-1jo7 — best single overview of all 12 `SearchType` values with code.
- **FalkorDB integration guide:** https://docs.falkordb.com/agentic-memory/cognee.html — if you want a faster graph backend than Kuzu.
- **Claude Code integration:** https://github.com/topoteretes/cognee-integration-claude — if your agent is built with the Anthropic SDK, this shows the session-hook pattern.
- **AGENTS.md in-repo:** https://github.com/topoteretes/cognee/blob/main/AGENTS.md — how the maintainers want agents to interact with the codebase (useful for any Claude/Copilot autogen).
- **Cognee blog — semantic search tactics:** https://www.cognee.ai/blog/deep-dives/the-art-of-intelligent-retrieval-unlocking-the-power-of-search

---

## Appendix: recommended hackathon decision tree

- **Need persistent memory across sessions, docs, or users?** Cognee is a good fit.
- **Only need chat-turn memory?** Skip it, use a dict or LangChain `ConversationBufferMemory`.
- **Only need plain semantic search over a small corpus?** Skip it, use Chroma/Qdrant directly. Cognee's overhead (LLM at ingest) isn't worth it.
- **Need to answer questions that span entities / relationships?** Cognee earns its keep here.
- **Worried about cost?** Stick to `gpt-4o-mini` default + Ollama for embeddings, or use Ollama for both.
- **Have <30 min to get something working?** Use the v1 API (`add`/`cognify`/`search`), the default stack, and a tiny sample document.
- **Demo audience cares about "AI memory that learns"?** This is Cognee's marketing wheelhouse — lean into it with the graph visualizer (`cognee.start_ui()`).
