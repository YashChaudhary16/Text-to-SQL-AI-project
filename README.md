# Ask the Warehouse: multi-agent text-to-SQL

Business users ask questions in plain English, such as *"Who were our best customers last year?"*, and get answers
from a complex retail data warehouse without knowing SQL. A LangGraph workflow of specialised agents works out
what they mean, asks when something is genuinely ambiguous, finds the right tables among 425 cryptically named
columns, then writes, safety-checks and runs the SQL. Every step streams live to the UI.

**Warehouse:** [TPC-DS](https://www.tpc.org/tpcds/), the industry-standard decision-support schema: 24 tables, 425
columns (`ss_ext_sales_price`, `cd_dep_college_count`, …), snowflake joins, and about 19M rows at scale factor 1.
It scales to billions of rows.

## How it works

New to the code? Start with the plain-language [tour](docs/how-it-works.md), then the
[architecture](docs/architecture.md) and the [data dictionary](docs/data-dictionary.md). Real problems we hit and
how we debugged them are logged in [challenges and fixes](docs/challenges-and-fixes.md).

```mermaid
flowchart LR
    Q([Question]) --> U[Understand<br/><i>intent · assumptions · table linking</i>]
    U -- ambiguous --> C{{Clarify<br/><i>interrupt → ask user</i>}}
    C -- answer --> U
    U -- out of scope --> X([Polite refusal])
    U --> R[Retrieve schema<br/><i>LLM linking + BM25 + join graph</i>]
    R -- schema question --> E([Explain the data])
    R --> W[Write SQL]
    W --> G[Guard + execute<br/><i>sqlglot allow-list · read-only · timeout</i>]
    G -- error --> W
    G -- ok --> A([Plain-English answer + table])
```

| Agent / step | What it does |
|---|---|
| **Understand** | Classifies intent, rewrites follow-ups into standalone questions, applies documented defaults (e.g. *"last year" = latest full year in the data*), and links the question to tables. |
| **Clarify** | When a question is ambiguous in a way that changes the answer (*"best" by revenue, orders or profit?*), the graph pauses with a LangGraph `interrupt` and resumes on the user's reply. State lives in a checkpointer, so this works across stateless HTTP requests. |
| **Retrieve** | Two-stage schema linking. The LLM picks tables from a compact catalog overview; BM25 over column descriptions and sample values adds column-level evidence; join-graph bridging adds connecting tables. Only that slice of the schema reaches the SQL writer. |
| **Write SQL** | Generates DuckDB SQL grounded in a **semantic layer**: business descriptions, metric definitions (*revenue = net_paid*), join rules and value domains. |
| **Guard + execute** | Rejects anything but a single read-only query over known tables (sqlglot AST checks), injects row limits, runs on a read-only connection with external access disabled and a timeout. Errors are fed back for self-correction. |

## Evaluation

The agents are scored against hand-verified gold SQL, comparing query **results**, not SQL text:
- **Dev set (41 questions):** 10 easy, 13 medium, 10 hard (4 adapted from official TPC-DS queries), 2
  ambiguous, and 6 schema / out-of-scope routing checks. Used to diagnose failures and design fixes.
- **Held-out set (22 questions):** written and committed *before* any fixes, never used to choose them, so it
  measures whether improvements generalise.

**Baseline** (gpt-oss-120b for SQL + gpt-oss-20b for intake, Groq free tier): **77.1%** on dev (27/35), routing
100% (41/41), median latency 3.3 s, $0 per question. All 8 dev failures were *valid SQL with the wrong
meaning*, e.g. counting order lines instead of distinct orders, or using a store manager where an item manager
was meant. The error-driven self-correction loop can't catch these.

**Fixes** (designed from dev failures only): terms grounded to metrics/columns before writing SQL, definitions
placed ahead of the schema, a "rethink" retry on empty results, and filled semantic-layer gaps.

| SQL model | Set | Before fixes | After fixes | Questions fixed / broken |
|---|---|---|---|---|
| gpt-oss-120b (Groq) | **held-out** | 70.0% (14/20) | **78.9%** (15/19\*) | 2 / 0 |
| gpt-oss-120b (Groq) | dev | 77.1% (27/35) | *pending (daily quota)* | |
| gpt-oss-20b (local, Ollama) | held-out | 80.0% (16/20) | 75.0% (15/20) | 3 / 4 |
| gpt-oss-20b (local, Ollama) | dev | 71.4% (25/35) | 74.3% (26/35) | 4 / 3 |

\*One held-out question is still waiting on the free tier's daily token quota.

**Reading the results honestly.**
- **Large model:** a clean gain on unseen questions, with nothing broken.
- **Small local model:** the same fixes churn (7 fixed, 7 broken) with no net effect, consistent with its larger
  run-to-run variance and weaker instruction-following.
- **Sample size:** at 20 questions, one question is 5 points. Repeated runs and a larger held-out set are needed
  before calling any of these differences significant.

Reports live in [`backend/evals/runs/`](backend/evals/runs/). Run it yourself with `uv run sqlagent eval`.

## Engineering highlights

- **Semantic layer as code**: a curated YAML glossary is merged with live warehouse introspection (types, value
  domains, date coverage, FK paths derived from naming conventions, since TPC-DS declares no foreign keys) into a
  versioned catalog.
- **Cost-aware, provider-agnostic LLM layer**: each node asks for a *role*, not a provider. The default is free
  open-weight models (Groq, Cerebras, local Ollama) with automatic fallback. Visitors can bring their own Claude
  key per request, and the owner's key is only an opt-in, capped last resort. Every call records the serving
  model and token usage.
- **Defence in depth for LLM-generated SQL**: AST allow-list, read-only connection, locked configuration, no
  filesystem or network access from SQL, statement timeouts and row caps, each covered by tests.
- **Secrets never persisted**: BYO keys travel through LangGraph's runtime context, not `configurable`, which
  LangGraph copies into checkpoint metadata.
- **Tested end to end**: graph and API tests drive the real warehouse with a scripted LLM, covering
  clarification round-trips, self-correction, unsafe-SQL rejection and follow-ups.

## Stack

FastAPI · LangGraph · LangChain · DuckDB / MotherDuck · sqlglot · rank-bm25 · Next.js 16 · React 19 · Tailwind 4,
with Postgres (Neon) for conversation checkpoints.

## Run it locally

Prerequisites: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, and either a free
[Groq API key](https://console.groq.com/keys) or [Ollama](https://ollama.com).

```bash
# Backend
cd backend
uv sync
uv run sqlagent generate-data --sf 1     # ~5 s, builds data/tpcds_sf1.duckdb
cp .env.example .env                     # add GROQ_API_KEY and/or OLLAMA_BASE_URL
uv run sqlagent ask "What were the top 5 product categories by revenue in 2002?"
uv run sqlagent serve                    # http://localhost:8000

# Frontend (second terminal)
cd frontend
npm install
cp .env.local.example .env.local
npm run dev                              # http://localhost:3000
```

Tests: `cd backend && uv run pytest`.

## Roadmap

- [x] Evaluation harness with a TPC-DS gold set (baseline 77.1%)
- [x] Held-out question set; first round of fixes for semantic failure modes
- [ ] Repeated runs per configuration (variance), a larger held-out set, and the remaining failure modes
      (versioned dimensions beyond products, overloaded terms, NULL keys in set operations)
- [ ] Accuracy-vs-cost comparison across Claude models; BIRD benchmark
- [ ] Semantic cache of verified question → SQL pairs (pgvector)
- [ ] Hosted demo: Vercel (UI) + MotherDuck (warehouse) + Neon (checkpoints)
- [ ] Automatic charts, an `EXPLAIN` dry-run validator, and a planner for multi-step questions

## License

MIT
