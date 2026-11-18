# Evaluation report: 2026-11-17-heldout-baseline

- **Date:** 2026-11-17T23:39 · **Git commit:** `2ccce55` · **Gold set:** tpcds_heldout.yaml
- **Router models:** `groq:openai/gpt-oss-20b`
- **SQL models:** `groq:openai/gpt-oss-120b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **71.4%** (10/14) |
| Execution accuracy, strict (no extra columns) | 42.9% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 2.745 s / 8.11 s |
| Tokens per question (in / out) | 4,126 / 977 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 1 |
| Provider fallbacks / rate-limit waits | 4 / 4 |
| Cases not scored (all providers unavailable) | 6 |

## By category

| Category | Accuracy |
|---|---|
| easy | 75.0% (3/4) |
| medium | 80.0% (4/5) |
| hard | 60.0% (3/5) |
| schema | 100.0% (1/1) |
| out_of_scope | 100.0% (1/1) |

## Model calls

| Model | Calls |
|---|---|
| `groq:openai/gpt-oss-120b` | 14 |
| `groq:openai/gpt-oss-20b` | 18 |

## Failures (4)

### x06 (easy): How many web pages does our website have?

- **Why:** no predicted column matches gold column 'pages'
- **Tables:** web_page

```sql
SELECT
  COUNT(*) AS total_pages
FROM web_page
LIMIT 501
```

### x12 (medium): What percentage of in-store revenue in 2002 came from preferred customers?

- **Why:** no predicted column matches gold column 'pct'
- **Tables:** store_sales, customer, customer_demographics, date_dim

```sql
WITH sales_2002 AS (
  SELECT
    ss.ss_net_paid,
    cd.cd_credit_rating
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  JOIN customer_demographics AS cd
    ON ss.ss_cdemo_sk = cd.cd_demo_sk
  WHERE
    d.d_year = 2002
)
SELECT
  ROUND(
    100.0 * SUM(CASE WHEN cd_credit_rating = 'Preferred' THEN ss_net_paid ELSE 0 END) / NULLIF(SUM(ss_net_paid), 0),
    2
  ) AS preferred_revenue_pct
FROM sales_2002
LIMIT 501
```

### x16 (hard): For each year from 1999 to 2002, what was catalog revenue growth versus the previous year, as a percentage?

- **Why:** no predicted column matches gold column 'growth_pct'
- **Tables:** catalog_sales, date_dim

```sql
WITH yearly AS (
  SELECT
    d.d_year AS year,
    SUM(cs.cs_net_paid) AS revenue
  FROM catalog_sales AS cs
  JOIN date_dim AS d
    ON cs.cs_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year BETWEEN 1998 AND 2002
  GROUP BY
    d.d_year
)
SELECT
  y.year,
  ROUND(
    (
      (
        y.revenue - LAG(y.revenue) OVER (ORDER BY y.year)
      ) / NULLIF(LAG(y.revenue) OVER (ORDER BY y.year), 0)
    ) * 100,
    2
  ) AS revenue_yoy_pct
FROM yearly AS y
WHERE
  y.year BETWEEN 1999 AND 2002
ORDER BY
  y.year
LIMIT 501
```

### x17 (hard): How many customers bought in our stores in 2001 but not in 2002?

- **Why:** no predicted column matches gold column 'customers'
- **Tables:** store_sales, date_dim, customer, customer_demographics

```sql
WITH purchases_2001 AS (
  SELECT DISTINCT
    ss_customer_sk
  FROM store_sales
  JOIN date_dim AS d
    ON ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year = 2001
), purchases_2002 AS (
  SELECT DISTINCT
    ss_customer_sk
  FROM store_sales
  JOIN date_dim AS d
    ON ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year = 2002
)
SELECT
  COUNT(*) AS distinct_customers
FROM purchases_2001 AS p1
WHERE
  NOT EXISTS(
    SELECT
      1
    FROM purchases_2002 AS p2
    WHERE
      p2.ss_customer_sk = p1.ss_customer_sk
  )
LIMIT 501
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
