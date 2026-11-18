# Evaluation report: 2026-11-17-ollama-postfix-heldout

- **Date:** 2026-11-18T10:53 · **Git commit:** `56bf07f` · **Gold set:** tpcds_heldout.yaml
- **Router models:** `ollama:gpt-oss:20b`
- **SQL models:** `ollama:gpt-oss:20b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **75.0%** (15/20) |
| Execution accuracy, strict (no extra columns) | 55.0% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 46.17 s / 68.75 s |
| Tokens per question (in / out) | 3,902 / 1,063 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 1 |
| Provider fallbacks / rate-limit waits | 0 / 0 |
| Cases not scored (all providers unavailable) | 0 |

## By category

| Category | Accuracy |
|---|---|
| easy | 83.3% (5/6) |
| medium | 87.5% (7/8) |
| hard | 50.0% (3/6) |
| schema | 100.0% (1/1) |
| out_of_scope | 100.0% (1/1) |

## Model calls

| Model | Calls |
|---|---|
| `ollama:gpt-oss:20b` | 44 |

## Failures (5)

### x06 (easy): How many web pages does our website have?

- **Why:** no predicted column matches gold column 'pages'
- **Tables:** web_page

```sql
SELECT
  COUNT(*) AS total_web_pages
FROM web_page
LIMIT 501
```

### x07 (medium): Which 5 states generated the most in-store revenue in 2002, based on the customer's address at the time of purchase?

- **Why:** no predicted column matches gold column 'revenue'
- **Tables:** store_sales, customer, customer_address, date_dim

```sql
SELECT
  a.ca_state AS state,
  ROUND(SUM(s.ss_net_paid), 2) AS total_revenue
FROM store_sales AS s
JOIN date_dim AS d
  ON s.ss_sold_date_sk = d.d_date_sk
JOIN customer AS c
  ON s.ss_customer_sk = c.c_customer_sk
JOIN customer_address AS a
  ON s.ss_addr_sk = a.ca_address_sk
WHERE
  d.d_year = 2002 AND NOT a.ca_state IS NULL
GROUP BY
  a.ca_state
ORDER BY
  total_revenue DESC
LIMIT 5
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
  JOIN date_dim AS d
    ON catalog_sales.cs_sold_date_sk = d.d_date_sk
  WHERE
    d_year BETWEEN 1998 AND 2002
  GROUP BY
    d_year
)
SELECT
  yr.year,
  ROUND(
    (
      yr.revenue - LAG(yr.revenue) OVER (ORDER BY yr.year)
    ) / NULLIF(LAG(yr.revenue) OVER (ORDER BY yr.year), 0) * 100,
    2
  ) AS revenue_growth_pct
FROM yearly_rev AS yr
WHERE
  yr.year BETWEEN 1999 AND 2002
ORDER BY
  yr.year
LIMIT 501
```

### x17 (hard): How many customers bought in our stores in 2001 but not in 2002?

- **Why:** no predicted column matches gold column 'customers'
- **Tables:** store_sales, date_dim, customer, store_returns

```sql
SELECT
  COUNT(*) AS customers_2001_not_2002
FROM (
  SELECT DISTINCT
    ss_customer_sk
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_year = 2001
) AS s2001
WHERE
  NOT EXISTS(
    SELECT
      1
    FROM store_sales AS ss2
    JOIN date_dim AS d2
      ON ss2.ss_sold_date_sk = d2.d_date_sk
    WHERE
      d2.d_year = 2002 AND ss2.ss_customer_sk = s2001.ss_customer_sk
  )
LIMIT 501
```

### x18 (hard): What was the average number of units per store receipt in 2002?

- **Why:** no predicted column matches gold column 'units_per_receipt'
- **Tables:** store_sales, date_dim

```sql
SELECT
  ROUND(total_rows * 1.0 / distinct_receipts, 2) AS avg_items_per_receipt
FROM (
  SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT ss_ticket_number) AS distinct_receipts
  FROM store_sales AS ss
  JOIN date_dim AS dd
    ON ss.ss_sold_date_sk = dd.d_date_sk
  WHERE
    dd.d_year = 2002
) AS t
LIMIT 501
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
