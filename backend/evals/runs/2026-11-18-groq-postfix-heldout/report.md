# Evaluation report: 2026-11-18-groq-postfix-heldout

- **Date:** 2026-11-18T11:22 · **Git commit:** `56bf07f` · **Gold set:** tpcds_heldout.yaml
- **Router models:** `groq:openai/gpt-oss-20b`
- **SQL models:** `groq:openai/gpt-oss-120b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **78.9%** (15/19) |
| Execution accuracy, strict (no extra columns) | 57.9% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 3.19 s / 5.47 s |
| Tokens per question (in / out) | 4,629 / 1,165 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 2 |
| Provider fallbacks / rate-limit waits | 0 / 0 |
| Cases not scored (all providers unavailable) | 1 |

## By category

| Category | Accuracy |
|---|---|
| easy | 83.3% (5/6) |
| medium | 87.5% (7/8) |
| hard | 60.0% (3/5) |
| schema | 100.0% (1/1) |
| out_of_scope | 100.0% (1/1) |

## Model calls

| Model | Calls |
|---|---|
| `groq:openai/gpt-oss-120b` | 19 |
| `groq:openai/gpt-oss-20b` | 24 |

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
WITH revenue_by_type AS (
  SELECT
    SUM(ss.ss_net_paid) AS total_rev,
    SUM(CASE WHEN cd.cd_credit_rating = '5' THEN ss.ss_net_paid ELSE 0 END) AS pref_rev
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  JOIN customer_demographics AS cd
    ON ss.ss_cdemo_sk = cd.cd_demo_sk
  WHERE
    d.d_year = 2002
)
SELECT
  ROUND((
    pref_rev / NULLIF(total_rev, 0)
  ) * 100, 2) AS preferred_revenue_pct
FROM revenue_by_type
LIMIT 501
```

### x16 (hard): For each year from 1999 to 2002, what was catalog revenue growth versus the previous year, as a percentage?

- **Why:** no predicted column matches gold column 'growth_pct'
- **Tables:** catalog_sales, date_dim

```sql
WITH yearly_rev AS (
  SELECT
    d_year AS year,
    SUM(cs_net_paid) AS revenue
  FROM catalog_sales
  JOIN date_dim
    ON catalog_sales.cs_sold_date_sk = date_dim.d_date_sk
  WHERE
    d_year BETWEEN 1998 AND 2002
  GROUP BY
    d_year
)
SELECT
  year,
  ROUND(revenue, 2) AS revenue,
  ROUND(
    (
      revenue - LAG(revenue) OVER (ORDER BY year)
    ) / LAG(revenue) OVER (ORDER BY year) * 100,
    2
  ) AS yoy_growth_pct
FROM yearly_rev
WHERE
  year BETWEEN 1999 AND 2002
ORDER BY
  year
LIMIT 501
```

### x17 (hard): How many customers bought in our stores in 2001 but not in 2002?

- **Why:** no predicted column matches gold column 'customers'
- **Tables:** store_sales, date_dim, customer, customer_demographics

```sql
WITH cust_2001 AS (
  SELECT DISTINCT
    ss.ss_customer_sk
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year = 2001
), cust_2002 AS (
  SELECT DISTINCT
    ss.ss_customer_sk
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year = 2002
)
SELECT
  COUNT(*) AS customer_count
FROM cust_2001 AS c1
LEFT JOIN cust_2002 AS c2
  ON c1.ss_customer_sk = c2.ss_customer_sk
WHERE
  c2.ss_customer_sk IS NULL
LIMIT 501
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
