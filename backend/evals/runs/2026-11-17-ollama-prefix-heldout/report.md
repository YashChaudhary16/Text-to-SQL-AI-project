# Evaluation report: 2026-11-17-ollama-prefix-heldout

- **Date:** 2026-11-18T10:35 · **Git commit:** `2ccce55` · **Gold set:** tpcds_heldout.yaml
- **Router models:** `ollama:gpt-oss:20b`
- **SQL models:** `ollama:gpt-oss:20b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **80.0%** (16/20) |
| Execution accuracy, strict (no extra columns) | 50.0% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 42.325 s / 72.12 s |
| Tokens per question (in / out) | 3,953 / 1,135 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 2 |
| Provider fallbacks / rate-limit waits | 0 / 0 |
| Cases not scored (all providers unavailable) | 0 |

## By category

| Category | Accuracy |
|---|---|
| easy | 66.7% (4/6) |
| medium | 87.5% (7/8) |
| hard | 83.3% (5/6) |
| schema | 100.0% (1/1) |
| out_of_scope | 100.0% (1/1) |

## Model calls

| Model | Calls |
|---|---|
| `ollama:gpt-oss:20b` | 45 |

## Failures (4)

### x05 (easy): What was the total refund amount for store returns in 2001?

- **Why:** no predicted column matches gold column 'refunds'
- **Tables:** store_returns, date_dim, catalog_returns, web_returns

```sql
SELECT
  ROUND(SUM(sr_return_amt_inc_tax), 2) AS total_refund_amount
FROM store_returns
JOIN date_dim
  ON store_returns.sr_returned_date_sk = date_dim.d_date_sk
WHERE
  d_year = 2001
LIMIT 501
```

### x06 (easy): How many web pages does our website have?

- **Why:** no predicted column matches gold column 'pages'
- **Tables:** web_page

```sql
SELECT
  COUNT(*) AS row_count
FROM web_page
LIMIT 501
```

### x12 (medium): What percentage of in-store revenue in 2002 came from preferred customers?

- **Why:** no predicted column matches gold column 'pct'
- **Tables:** store_sales, customer, customer_demographics, date_dim

```sql
SELECT
  ROUND(
    100.0 * SUM(CASE WHEN cd.cd_credit_rating IN ('4', '5') THEN ss.ss_net_paid ELSE 0 END) / SUM(ss.ss_net_paid),
    2
  ) AS percentage_preferred_revenue
FROM store_sales AS ss
JOIN date_dim AS d
  ON ss.ss_sold_date_sk = d.d_date_sk
JOIN customer_demographics AS cd
  ON ss.ss_cdemo_sk = cd.cd_demo_sk
WHERE
  d.d_year = 2002
LIMIT 501
```

### x15 (hard): Which 10 customers returned the most units to our stores? Show their customer IDs.

- **Why:** no predicted column matches gold column 'c_customer_id'
- **Tables:** store_returns, customer, reason, date_dim

```sql
SELECT
  c.c_customer_id AS customer_id,
  COUNT(*) AS return_count
FROM store_returns AS sr
JOIN customer AS c
  ON sr.sr_customer_sk = c.c_customer_sk
GROUP BY
  c.c_customer_id
ORDER BY
  return_count DESC
LIMIT 10
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
