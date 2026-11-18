# Evaluation report: 2026-11-17-groq-gpt-oss-120b

- **Date:** 2026-11-17T22:47 · **Git commit:** `c4175ab` · **Gold set:** tpcds_gold.yaml
- **Router models:** `groq:openai/gpt-oss-20b`
- **SQL models:** `groq:openai/gpt-oss-120b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **77.1%** (27/35) |
| Execution accuracy, strict (no extra columns) | 57.1% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 3.26 s / 17.99 s |
| Tokens per question (in / out) | 4,547 / 1,122 |
| SQL retries: needed / rescued | 0 / 0 |
| Clarifying questions asked | 3 |
| Provider fallbacks / rate-limit waits | 12 / 12 |
| Cases not scored (all providers unavailable) | 0 |

## By category

| Category | Accuracy |
|---|---|
| easy | 80.0% (8/10) |
| medium | 84.6% (11/13) |
| hard | 60.0% (6/10) |
| ambiguous | 100.0% (2/2) |
| schema | 100.0% (3/3) |
| out_of_scope | 100.0% (3/3) |

## Model calls

| Model | Calls |
|---|---|
| `groq:openai/gpt-oss-120b` | 35 |
| `groq:openai/gpt-oss-20b` | 47 |

## Failures (8)

### e05 (easy): How many web orders were placed in 2000?

- **Why:** no predicted column matches gold column 'orders'
- **Tables:** web_sales, date_dim, web_page, catalog_sales

```sql
SELECT
  COUNT(*) AS web_line_items_2000
FROM web_sales AS ws
JOIN date_dim AS d
  ON ws.ws_sold_date_sk = d.d_date_sk
WHERE
  d.d_year = 2000
LIMIT 501
```

### e07 (easy): How many of our customers are preferred customers?

- **Why:** no predicted column matches gold column 'preferred'
- **Tables:** customer, customer_demographics

```sql
SELECT
  COUNT(DISTINCT c.c_customer_sk) AS credit_rating_a_customers
FROM customer AS c
JOIN customer_demographics AS cd
  ON c.c_current_cdemo_sk = cd.cd_demo_sk
WHERE
  cd.cd_credit_rating = 'A'
LIMIT 501
```

### h01 (hard): What is the store return rate (units returned divided by units sold) for each product category?

- **Why:** row count 12 != gold 10
- **Tables:** store_sales, store_returns, item, catalog_returns, date_dim

```sql
WITH sales AS (
  SELECT
    i.i_category AS category,
    SUM(ss.ss_quantity) AS units_sold
  FROM store_sales AS ss
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  GROUP BY
    i.i_category
), returns AS (
  SELECT
    i.i_category AS category,
    SUM(sr.sr_return_quantity) AS units_returned
  FROM store_returns AS sr
  JOIN item AS i
    ON sr.sr_item_sk = i.i_item_sk
  GROUP BY
    i.i_category
)
SELECT
  COALESCE(s.category, r.category) AS category,
  COALESCE(s.units_sold, 0) AS units_sold,
  COALESCE(r.units_returned, 0) AS units_returned,
  ROUND(
    CAST(COALESCE(r.units_returned, 0) AS DOUBLE) / NULLIF(COALESCE(s.units_sold, 0), 0),
    4
  ) AS return_rate
FROM sales AS s
FULL OUTER JOIN returns AS r
  ON s.category = r.category
ORDER BY
  return_rate DESC
LIMIT 500
```

### h06 (hard): For products from manufacturer 128, what were in-store gross sales by year and brand, counting only November sales? Show brand ID, brand and sales, top 100 by year then sales.

- **Why:** no predicted column matches gold column 'i_brand_id'
- **Tables:** store_sales, date_dim, item

```sql
SELECT
  d.d_year AS year,
  i.i_brand_id AS brand_id,
  i.i_brand AS brand,
  ROUND(SUM(ss.ss_net_paid), 2) AS gross_sales
FROM store_sales AS ss
JOIN date_dim AS d
  ON ss.ss_sold_date_sk = d.d_date_sk
JOIN item AS i
  ON ss.ss_item_sk = i.i_item_sk
WHERE
  i.i_manufact_id = 128 AND d.d_moy = 11
GROUP BY
  d.d_year,
  i.i_brand_id,
  i.i_brand
ORDER BY
  d.d_year,
  gross_sales DESC
LIMIT 100
```

### h07 (hard): For items managed by manager 1, what were in-store gross sales by category in November 2000?

- **Why:** row count 11 != gold 10
- **Tables:** store_sales, store, item, date_dim, store_returns

```sql
WITH sales AS (
  SELECT
    ss.ss_ext_sales_price,
    i.i_category
  FROM store_sales AS ss
  JOIN store AS s
    ON ss.ss_store_sk = s.s_store_sk
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  WHERE
    s.s_manager = 'Edwin Adams' AND d.d_year = 2000 AND d.d_moy = 11
)
SELECT
  i_category AS category,
  ROUND(SUM(ss_ext_sales_price), 2) AS gross_sales
FROM sales
GROUP BY
  i_category
ORDER BY
  gross_sales DESC
LIMIT 500
```

### h08 (hard): For manager 28's items, which brands had the highest in-store gross sales in November 1999? Show brand ID, brand and sales for the top 100.

- **Why:** row count 0 != gold 100
- **Tables:** store_sales, store, item, date_dim

```sql
WITH filtered AS (
  SELECT
    ss.ss_item_sk,
    ss.ss_net_paid,
    i.i_brand_id,
    i.i_brand
  FROM store_sales AS ss
  JOIN store AS s
    ON ss.ss_store_sk = s.s_store_sk
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  WHERE
    d.d_year = 1999 AND d.d_moy = 11 AND s.s_manager = '28'
)
SELECT
  i_brand_id AS brand_id,
  i_brand AS brand,
  ROUND(SUM(ss_net_paid), 2) AS gross_sales
FROM filtered
GROUP BY
  i_brand_id,
  i_brand
ORDER BY
  gross_sales DESC
LIMIT 100
```

### m04 (medium): What are the top 5 return reasons by total return amount across all channels?

- **Why:** no predicted column matches gold column 'r_reason_desc'
- **Tables:** store_returns, catalog_returns, web_returns, reason, date_dim

```sql
WITH store AS (
  SELECT
    r.r_reason_desc AS reason_desc,
    sr.sr_return_amt_inc_tax AS return_amount
  FROM store_returns AS sr
  JOIN reason AS r
    ON sr.sr_reason_sk = r.r_reason_sk
), catalog AS (
  SELECT
    r.r_reason_desc AS reason_desc,
    cr.cr_return_amt_inc_tax AS return_amount
  FROM catalog_returns AS cr
  JOIN reason AS r
    ON cr.cr_reason_sk = r.r_reason_sk
), web AS (
  SELECT
    r.r_reason_desc AS reason_desc,
    wr.wr_return_amt_inc_tax AS return_amount
  FROM web_returns AS wr
  JOIN reason AS r
    ON wr.wr_reason_sk = r.r_reason_sk
), combined AS (
  SELECT
    reason_desc,
    return_amount
  FROM store
  UNION ALL
  SELECT
    reason_desc,
    return_amount
  FROM catalog
  UNION ALL
  SELECT
    reason_desc,
    return_amount
  FROM web
)
SELECT
  reason_desc,
  ROUND(SUM(return_amount), 2) AS total_return_amount
FROM combined
GROUP BY
  reason_desc
ORDER BY
  total_return_amount DESC
LIMIT 5
```

### m06 (medium): Which household income band generated the most in-store revenue? Show its lower and upper bound.

- **Why:** no predicted column matches gold column 'ib_lower_bound'
- **Tables:** store_sales, customer, household_demographics, income_band, date_dim

```sql
WITH revenue_by_income AS (
  SELECT
    ib.ib_lower_bound AS income_lower,
    ib.ib_upper_bound AS income_upper,
    ROUND(SUM(ss.ss_net_paid), 2) AS revenue
  FROM store_sales AS ss
  JOIN customer AS c
    ON ss.ss_customer_sk = c.c_customer_sk
  JOIN household_demographics AS hd
    ON c.c_current_hdemo_sk = hd.hd_demo_sk
  JOIN income_band AS ib
    ON hd.hd_income_band_sk = ib.ib_income_band_sk
  GROUP BY
    ib.ib_lower_bound,
    ib.ib_upper_bound
)
SELECT
  income_lower,
  income_upper,
  revenue
FROM revenue_by_income
ORDER BY
  revenue DESC
LIMIT 1
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
