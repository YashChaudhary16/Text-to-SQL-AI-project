# Evaluation report: 2026-11-18-groq-postfix-dev

- **Date:** 2026-11-18T11:23 · **Git commit:** `56bf07f` · **Gold set:** tpcds_gold.yaml
- **Router models:** `groq:openai/gpt-oss-20b`
- **SQL models:** `groq:openai/gpt-oss-120b`

> ⚠️ **Incomplete run:** 33 of 41 cases were not scored (no LLM provider available). Resume with the same `--name` before quoting these numbers.

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **100.0%** (3/3) |
| Execution accuracy, strict (no extra columns) | 100.0% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 1.935 s / 3.48 s |
| Tokens per question (in / out) | 2,846 / 532 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 0 |
| Provider fallbacks / rate-limit waits | 1 / 1 |
| Cases not scored (all providers unavailable) | 33 |

## By category

| Category | Accuracy |
|---|---|
| easy | 100.0% (2/2) |
| medium | 100.0% (1/1) |
| schema | 100.0% (3/3) |
| out_of_scope | 100.0% (2/2) |

## Model calls

| Model | Calls |
|---|---|
| `groq:openai/gpt-oss-120b` | 3 |
| `groq:openai/gpt-oss-20b` | 11 |

## Failures (0)

None.
## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
