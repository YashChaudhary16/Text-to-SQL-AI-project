# CLAUDE.md

Natural-language → SQL assistant that lets a **non-technical user query a complex enterprise data warehouse**. Portfolio/resume project: favor production-grade structure, tests, observability and measurable accuracy over quick hacks.

## Commands (run from `backend/`)

```bash
uv sync                                   # install (Python 3.12)
uv run sqlagent generate-data --sf 1      # TPC-DS -> data/tpcds_sf1.duckdb (~5s, 315 MB, gitignored)
uv run sqlagent build-catalog             # semantic/tpcds.yaml + warehouse -> semantic/tpcds.catalog.json
uv run sqlagent ask "top 5 categories by revenue in 2002"   # full graph in the terminal
uv run sqlagent serve --reload            # API on :8000
uv run pytest -q                          # graph/API tests use a scripted fake LLM + the real warehouse
uv run ruff format src tests && uv run ruff check src tests
```

After editing `semantic/tpcds.yaml`, run `build-catalog` and then `uv run sqlagent docs`, which regenerates the column reference in `docs/data-dictionary.md` between its GENERATED markers. Edit only the prose outside those markers. The generated JSON is committed, so the server never introspects the warehouse at startup.

Docs: `docs/architecture.md` (system design and decisions; keep it current when behaviour changes) and `docs/data-dictionary.md`.

## Backend layout (`backend/src/sqlagent/`)

- `config.py`: pydantic-settings and all tunables; `.env.example` documents the env vars.
- `semantic/`: `catalog.py` (models + prompt rendering), `builder.py` (YAML + introspection → catalog). Curated knowledge lives in `backend/semantic/tpcds.yaml`.
- `retrieval.py`: two-stage schema linking. Stage 1: the router LLM picks tables from the catalog overview. Stage 2: BM25 adds strong column-level matches that join directly to those tables. Then join-graph bridging, which never routes through `date_dim`/`time_dim`.
- `db/guard.py` (sqlglot allow-list checks + LIMIT injection) and `db/warehouse.py` (read-only DuckDB, external access off, locked config, timeout via `interrupt()`).
- `llm.py`: role-based provider chains with fallback, recording the serving model and token usage per call.
- `graph/`: `state.py` (state, `RunContext`, structured outputs), `prompts.py`, `agent.py` (nodes + graph).
- `api/app.py`: FastAPI. `POST /api/chat` streams SSE events (`thread`, `step`, `clarification`, `result`, `answer`, `error`, `done`). It resumes automatically when the thread is paused on a clarification.
- `cli.py`: the `sqlagent` CLI.

## Gotchas

- **Never put secrets in `config["configurable"]`.** LangGraph copies its string values into checkpoint metadata (i.e. Postgres). Per-request secrets go through `context=RunContext(...)`.
- DuckDB shares one database instance per file within a process, so every local connection must pass identical config. Security settings are passed via `config=` at connect, not `SET` afterwards.
- Node state updates overwrite (there are no reducers). Nodes that extend lists (`history`, `llm_calls`, `clarifications`) return `[*old, new]`. The API resets per-turn fields with `new_turn()`.

## Target architecture (decided)

```
[Vercel: Next.js chat UI] --SSE stream of agent steps--> [FastAPI + LangGraph (Python)]
                                                             |            |
                                       [MotherDuck: TPC-DS warehouse]  [Neon Postgres: LangGraph
                                        (read-only, DuckDB dialect)     checkpoints + pgvector schema index]
```

- **Frontend**: Next.js (App Router, TypeScript) on Vercel. Streams each graph node's progress, shows the generated SQL, a results table, and clarification prompts.
- **Backend**: FastAPI + LangGraph, Python. Deploy target is the Vercel Python runtime; fall back to Render's free web service if bundle-size or timeout limits bite.
- **Warehouse**: TPC-DS (24 tables, ~425 cryptically named columns such as `ss_sold_date_sk`) on **MotherDuck** (free: 10 GB storage, 10 compute-hours/month). Local dev uses an embedded DuckDB file with the same dialect. Generate it with DuckDB's `tpcds` extension (`CALL dsdgen(sf=N)`). Use SF1 locally (~2.9M `store_sales` rows) and the largest SF that fits the free tier in the hosted demo.
- **App state**: **Neon** Postgres (free: 1 GB/project) for the LangGraph checkpointer, which human-in-the-loop interrupts need across stateless requests, and pgvector for the schema index.
- **Hard constraint: everything must stay on free tiers.** Check any new service against this before adding it. Protect the hosted demo's LLM spend and MotherDuck compute (rate limiting, statement timeouts, row limits, result caching).

## LangGraph workflow

v1 scope (build first):
1. **intent / clarify** (`router` role): classify the question (data query vs. schema question vs. out-of-scope) and detect ambiguity ("top customers" by what? which period?). If ambiguous, pause with a LangGraph `interrupt()` and resume once the user answers.
2. **schema retrieval (RAG)**: retrieve relevant tables/columns from an index built over the **semantic layer** (`semantic/tpcds.yaml`: business-friendly descriptions, synonyms, join paths, metric definitions such as "revenue", plus sample values). Only that slice of the schema goes to the generator, never all 425 columns.
3. **SQL generation** (`sql` role): DuckDB dialect, grounded in the retrieved slice.
4. **guarded execution**: parse with `sqlglot`, allow only a single read-only `SELECT`/`WITH`, inject `LIMIT`, enforce a statement timeout. This is non-negotiable even in v1 because the demo is public. On error, the error is fed back to the SQL writer (max `MAX_SQL_ATTEMPTS`; escalates to Claude from attempt 3 if a key is available).
5. **answer**: plain-English summary + table.

v2: `EXPLAIN` dry-run validator, query planner/decomposer for multi-step questions, semantic cache, chart recommendation, LangSmith tracing.

## Evaluation

- **BIRD** dev set (bird-bench.github.io): report execution accuracy (EX) as the headline resume metric.
- **TPC-DS gold set**: the `tpcds` extension ships the 99 official queries (`tpcds_queries()`). Write NL paraphrases of them to get a domain-specific gold set against our own warehouse.
- Don't call our numbers "TPC-DS benchmark results" (the TPC has publication rules). Say "evaluated on questions derived from TPC-DS queries".

## LLM providers & cost

- **Provider-agnostic LLM layer**: nodes never hard-code a provider. Each node asks a small factory (built on `init_chat_model`) for a model by *role* (`router`, `sql`), and gets back a chain built with `.with_fallbacks()`.
- **Default = free open-weight models** on hosted free tiers: Groq first (e.g. `gpt-oss-120b` for `sql`, a small Llama for `router`), then Cerebras or OpenRouter `:free` models as overflow. Rate limits are per model, so spreading roles across models spreads the quota. Local dev can use Ollama.
- **Claude is an opt-in upgrade, never the free default.** Order: the visitor's own key, if supplied (BYO), takes priority for that request. Otherwise the open-model chain runs. The owner's `ANTHROPIC_API_KEY` is used only when explicitly enabled, behind a hard spend cap set in the Anthropic Console. Escalate to Claude on 429/quota exhaustion or after the open model fails validation/execution twice.
- **BYO keys**: sent per request in a header over HTTPS, held only in browser memory/sessionStorage, never persisted, logged or traced server-side.
- **A Claude Pro/Max subscription cannot power this app.** Subscription OAuth is for personal use of Claude's own tools, so API usage is billed separately through the Console.
- Cost levers, in priority order: (1) semantic cache of question→verified SQL in pgvector, which also serves as retrieved few-shot examples; (2) schema RAG to keep prompts small; (3) per-IP rate limits; (4) cheapest-model-that-passes-eval per role; (5) prompt caching on the static system prompt when on Claude; (6) the Batch API for Claude eval runs.
- The eval harness should output an **accuracy vs. cost-per-question table per model**. That table drives model choice and is a key showcase artifact.

## Claude API conventions (when the Claude path is used)

- Use `langchain-anthropic` (native, not via an OpenAI-compatible shim). Never raw `requests`. The env var is `ANTHROPIC_API_KEY`.
- Models: `claude-opus-5-5` for SQL generation and planning; `claude-haiku-5-5` for routing and clarification. Use the IDs exactly as written, with no date suffixes.
- Current models **reject `temperature`/`top_p`/`top_k`** and **reject forced `tool_choice` (`any`/`tool`)** with a 400. Don't set sampling params. For structured output, use native structured outputs (`method="json_schema"` in `with_structured_output`) rather than function-calling mode, which forces tool choice.
- Don't use assistant-message prefill (also a 400). Control output shape with structured outputs or the system prompt.

## Repo conventions (for the new code)

- Top level: `backend/` (Python package `sqlagent`), `frontend/` (Next.js), `backend/evals/` (planned).
- Frontend: Next.js **16** with `cacheComponents` on. APIs differ from older versions, so read `frontend/AGENTS.md` and the docs in `frontend/node_modules/next/dist/docs/` before changing Next-specific code. `page.tsx` is a Server Component that renders the single client entry `components/App.tsx`. The SSE client is in `lib/api.ts` (fetch-based, since EventSource can't POST), and conversation state is in `lib/useChat.ts`. The BYO key lives in sessionStorage via `lib/keyStore.ts` (`useSyncExternalStore`). Check with `npx tsc --noEmit && npm run lint && npm run build`. Colors are CSS-variable tokens in `globals.css` with a dark-mode override; use `bg-surface`, `text-muted`, `border-border` and so on, not raw colors.
- Python: `uv` + `pyproject.toml`, type hints, `ruff` (line length 110), `pytest`. Secrets live only in `backend/.env`, never committed.
- New graph behaviour gets a test in `tests/test_graph.py` using `ScriptedLLM` (see `conftest.py`). Don't make live LLM calls in tests.
- Treat all LLM-generated SQL as untrusted input.
