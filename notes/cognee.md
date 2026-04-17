# Cognee — Hard Facts

Condensed fact sheet. See `cognee-dossier.md` for the full version.

## What it is
Open-source memory layer for AI agents. Ingest text → LLM extracts entities and relationships → stored as a knowledge graph (Kuzu) + vector index (LanceDB) + metadata (SQLite). Query in natural language; answers are graph-grounded, not bag-of-chunks RAG.

## Repo / docs
- GitHub: https://github.com/topoteretes/cognee
- Docs: https://docs.cognee.ai
- PyPI: https://pypi.org/project/cognee

## License
- Apache 2.0.

## Version & activity
- First stable: **1.0.0 on 2026-04-11** (6 days before this note).
- Currently on dev releases (`1.0.1.dev*`) landing multiple times per week.
- Active maintenance, but breaking changes plausible — **pin your version** for the hackathon.

## Install
```bash
uv add cognee
# or: pip install cognee
```

## Two API generations (coexist)
- **v1** — most tutorials, docs, and LLM-generated snippets use this:
  - `cognee.add(text, dataset_name=...)`
  - `cognee.cognify(...)`  ← builds the graph
  - `cognee.search(query, search_type=...)`
  - `cognee.prune()`
- **v2** — new with 1.0.0, README now leads with it:
  - `cognee.remember(...)`
  - `cognee.recall(...)`
  - `cognee.forget(...)`
  - `cognee.improve(...)`

⚠ LLMs will mix the two. Pick one and verify imports.

## Storage (zero-config)
Works out of the box with embedded stores — no Docker, no external DBs:
- **SQLite** — metadata
- **LanceDB** — vectors
- **Kuzu** — graph

Production swap-ins: Neo4j, Postgres, Qdrant, etc.

## LLM providers
- OpenAI (default, works end-to-end).
- Anthropic supported **for LLM only** — no embeddings API, so `LLM_PROVIDER=anthropic` silently falls back to OpenAI for embeddings. Need `OPENAI_API_KEY` set or it 401s.
- Local models via Ollama / LiteLLM configurable.

## Concurrency
- **Fully async.** No sync wrappers. Fine for FastAPI, painful for sync Flask/Django.

## Costs (watch out)
- `cognify` calls the LLM per chunk for entity extraction — **not just embeddings**.
- Default model is `gpt-4o-mini` for a reason: cents/page vs dollars/page on `gpt-4o`.
- Ingesting hundreds of pages on larger models can rack up $10–30+.

## Latency
- **~30s–2min per 5-page doc** for `cognify` — known N+1 query bug.
- Rules out ingesting a full curriculum live during a demo.
- For hackathon: seed narrowly (one course / a few weeks of material).

## Minimum env vars
```
LLM_PROVIDER=openai         # or anthropic/ollama
LLM_API_KEY=sk-...
EMBEDDING_API_KEY=sk-...    # OpenAI key even if LLM provider is Anthropic
```

## Quickstart (v1 API)
```python
import asyncio
import cognee

async def main():
    await cognee.add("Transformers use self-attention to model token interactions.", "ml-notes")
    await cognee.cognify()
    result = await cognee.search(
        query_text="how do transformers process tokens?",
        query_type=cognee.SearchType.GRAPH_COMPLETION,
    )
    print(result)

asyncio.run(main())
```

## FastAPI integration
- All cognee calls are `async def` — use directly in FastAPI routes.
- Initialize storage on startup (`lifespan` context) to avoid first-request lag.
- Don't block the event loop with sync file reads before `cognee.add`.

## Hackathon-fit verdict
- ✅ Zero-config works, Apache 2.0, FastAPI-friendly, plays to "memory/graph" narrative.
- ⚠ Slow ingest, real LLM cost, API churn — seed narrowly and pin the version.
