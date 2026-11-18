# Evaluation report: 2026-11-17-ollama-postfix-dev

- **Date:** 2026-11-18T12:17 · **Git commit:** `db3ef38` · **Gold set:** tpcds_gold.yaml
- **Router models:** `ollama:gpt-oss:20b`
- **SQL models:** `ollama:gpt-oss:20b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **74.3%** (26/35) |
| Execution accuracy, strict (no extra columns) | 62.9% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 44.48 s / 147.66 s |
| Tokens per question (in / out) | 4,691 / 1,388 |
| SQL retries: needed / rescued | 3 / 0 |
| Clarifying questions asked | 3 |
| Provider fallbacks / rate-limit waits | 0 / 0 |
| Cases not scored (all providers unavailable) | 0 |

## By category

| Category | Accuracy |
|---|---|
| easy | 90.0% (9/10) |
| medium | 84.6% (11/13) |
| hard | 60.0% (6/10) |
| ambiguous | 0.0% (0/2) |
| schema | 100.0% (3/3) |
| out_of_scope | 100.0% (3/3) |

## Model calls

| Model | Calls |
|---|---|
| `ollama:gpt-oss:20b` | 86 |

## Failures (9)

### a01 (ambiguous): Who are our best customers?

- **Why:** row count 9 != gold 10
- **Tables:** store_sales, catalog_sales, web_sales, customer, date_dim

```sql
WITH ss_rev AS (
  SELECT
    ss_customer_sk AS cust_sk,
    SUM(ss_net_paid) AS rev
  FROM store_sales
  GROUP BY
    ss_customer_sk
), cs_rev AS (
  SELECT
    cs_bill_customer_sk AS cust_sk,
    SUM(cs_net_paid) AS rev
  FROM catalog_sales
  GROUP BY
    cs_bill_customer_sk
), ws_rev AS (
  SELECT
    ws_bill_customer_sk AS cust_sk,
    SUM(ws_net_paid) AS rev
  FROM web_sales
  GROUP BY
    ws_bill_customer_sk
), all_rev AS (
  SELECT
    cust_sk,
    rev
  FROM ss_rev
  UNION ALL
  SELECT
    cust_sk,
    rev
  FROM cs_rev
  UNION ALL
  SELECT
    cust_sk,
    rev
  FROM ws_rev
), rev_sum AS (
  SELECT
    cust_sk,
    SUM(rev) AS total_rev
  FROM all_rev
  GROUP BY
    cust_sk
), top10 AS (
  SELECT
    cust_sk,
    total_rev
  FROM rev_sum
  ORDER BY
    total_rev DESC
  LIMIT 10
)
SELECT
  c.c_customer_id AS customer_id
FROM top10 AS t
JOIN customer AS c
  ON t.cust_sk = c.c_customer_sk
ORDER BY
  t.total_rev DESC
LIMIT 10
```

### a02 (ambiguous): What were our top products last year?

- **Why:** no predicted column matches gold column 'i_item_id'
- **Tables:** store_sales, catalog_sales, web_sales, item, date_dim

```sql
WITH ss AS (
  SELECT
    i.i_item_id,
    SUM(ss.ss_net_paid) AS rev
  FROM store_sales AS ss
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_date BETWEEN '2002-01-09' AND '2003-01-08' AND NOT i.i_item_id IS NULL
  GROUP BY
    i.i_item_id
), cs AS (
  SELECT
    i.i_item_id,
    SUM(cs.cs_net_paid) AS rev
  FROM catalog_sales AS cs
  JOIN item AS i
    ON cs.cs_item_sk = i.i_item_sk
  JOIN date_dim AS d
    ON cs.cs_sold_date_sk = d.d_date_sk
  WHERE
    d.d_date BETWEEN '2002-01-09' AND '2003-01-08' AND NOT i.i_item_id IS NULL
  GROUP BY
    i.i_item_id
), ws AS (
  SELECT
    i.i_item_id,
    SUM(ws.ws_net_paid) AS rev
  FROM web_sales AS ws
  JOIN item AS i
    ON ws.ws_item_sk = i.i_item_sk
  JOIN date_dim AS d
    ON ws.ws_sold_date_sk = d.d_date_sk
  WHERE
    d.d_date BETWEEN '2002-01-09' AND '2003-01-08' AND NOT i.i_item_id IS NULL
  GROUP BY
    i.i_item_id
), all_rev AS (
  SELECT
    i_item_id,
    SUM(rev) AS total_rev
  FROM (
    SELECT
      *
    FROM ss
    UNION ALL
    SELECT
      *
    FROM cs
    UNION ALL
    SELECT
      *
    FROM ws
  ) AS t
  GROUP BY
    i_item_id
)
SELECT
  a.i_item_id,
  MAX(i.i_product_name) AS product_name,
  ROUND(a.total_rev, 2) AS total_revenue
FROM all_rev AS a
JOIN item AS i
  ON a.i_item_id = i.i_item_id
GROUP BY
  a.i_item_id,
  a.total_rev
ORDER BY
  a.total_rev DESC
LIMIT 10
```

### e07 (easy): How many of our customers are preferred customers?

- **Why:** no predicted column matches gold column 'preferred'
- **Tables:** store_sales, catalog_sales, web_sales, customer, promotion, store, date_dim

```sql
WITH all_sales AS (
  SELECT
    ss_customer_sk AS customer_sk,
    ss_net_paid AS spend
  FROM store_sales
  UNION ALL
  SELECT
    cs_bill_customer_sk,
    cs_net_paid
  FROM catalog_sales
  UNION ALL
  SELECT
    ws_bill_customer_sk,
    ws_net_paid
  FROM web_sales
), customer_spend AS (
  SELECT
    customer_sk,
    SUM(spend) AS total_spend
  FROM all_sales
  WHERE
    NOT customer_sk IS NULL
  GROUP BY
    customer_sk
), ranked AS (
  SELECT
    customer_sk,
    total_spend,
    ROW_NUMBER() OVER (ORDER BY total_spend DESC) AS rn,
    COUNT(*) OVER () AS total_customers
  FROM customer_spend
)
SELECT
  COUNT(*) AS num_top_customers
FROM ranked
WHERE
  rn <= CEIL(total_customers * 0.1)
LIMIT 501
```

### h04 (hard): Who are the top 10 customers by total revenue across all channels? Show their customer IDs.

- **Why:** no predicted column matches gold column 'c_customer_id'
- **Tables:** store_sales, catalog_sales, web_sales, customer, date_dim

```sql
WITH store_rev AS (
  SELECT
    ss_customer_sk AS customer_id,
    ss_net_paid AS revenue
  FROM store_sales
  WHERE
    NOT ss_customer_sk IS NULL
), catalog_rev AS (
  SELECT
    cs_bill_customer_sk AS customer_id,
    cs_net_paid AS revenue
  FROM catalog_sales
  WHERE
    NOT cs_bill_customer_sk IS NULL
), web_rev AS (
  SELECT
    ws_bill_customer_sk AS customer_id,
    ws_net_paid AS revenue
  FROM web_sales
  WHERE
    NOT ws_bill_customer_sk IS NULL
), all_rev AS (
  SELECT
    customer_id,
    revenue
  FROM store_rev
  UNION ALL
  SELECT
    customer_id,
    revenue
  FROM catalog_rev
  UNION ALL
  SELECT
    customer_id,
    revenue
  FROM web_rev
), agg_rev AS (
  SELECT
    customer_id,
    SUM(revenue) AS total_revenue
  FROM all_rev
  GROUP BY
    customer_id
)
SELECT
  customer_id,
  ROUND(total_revenue, 2) AS total_revenue
FROM agg_rev
ORDER BY
  total_revenue DESC
LIMIT 10
```

### h06 (hard): For products from manufacturer 128, what were in-store gross sales by year and brand, counting only November sales? Show brand ID, brand and sales, top 100 by year then sales.

- **Why:** row count 9 != gold 89
- **Tables:** store_sales, item, date_dim

```sql
WITH sales_by_brand AS (
  SELECT
    i.i_manufact_id AS brand_id,
    i.i_manufact AS brand_name,
    d.d_year AS year,
    SUM(ss.ss_net_paid) AS gross_sales
  FROM store_sales AS ss
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  WHERE
    d.d_moy = 11 AND i.i_manufact_id = 128
  GROUP BY
    i.i_manufact_id,
    i.i_manufact,
    d.d_year
), ranked_sales AS (
  SELECT
    brand_id,
    brand_name,
    year,
    gross_sales,
    RANK() OVER (PARTITION BY year ORDER BY gross_sales DESC) AS sales_rank
  FROM sales_by_brand
)
SELECT
  brand_id,
  brand_name,
  year,
  ROUND(gross_sales, 2) AS gross_sales,
  sales_rank
FROM ranked_sales
WHERE
  sales_rank <= 100
ORDER BY
  year,
  sales_rank
LIMIT 500
```

### h07 (hard): For items managed by manager 1, what were in-store gross sales by category in November 2000?

- **Why:** row count 0 != gold 10
- **Tables:** store_sales, store, item, date_dim

```sql
SELECT
  i_category AS product_category,
  ROUND(SUM(ss_ext_sales_price), 2) AS gross_sales
FROM store_sales
JOIN date_dim
  ON store_sales.ss_sold_date_sk = date_dim.d_date_sk
JOIN store
  ON store_sales.ss_store_sk = store.s_store_sk
JOIN item
  ON store_sales.ss_item_sk = item.i_item_sk
WHERE
  date_dim.d_year = 2000 AND date_dim.d_moy = 11 AND store.s_manager = '1'
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
SELECT
  brand_id,
  brand_name,
  ROUND(total_gross_sales, 2) AS total_gross_sales
FROM (
  SELECT
    i.i_brand_id AS brand_id,
    i.i_brand AS brand_name,
    SUM(ss.ss_net_paid) AS total_gross_sales
  FROM store_sales AS ss
  JOIN date_dim AS d
    ON ss.ss_sold_date_sk = d.d_date_sk
  JOIN store AS s
    ON ss.ss_store_sk = s.s_store_sk
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  WHERE
    d.d_year = 1999 AND d.d_moy = 11 AND s.s_manager = '28'
  GROUP BY
    i.i_brand_id,
    i.i_brand
) AS t
ORDER BY
  total_gross_sales DESC
LIMIT 100
```

### m01 (medium): How many stores are there?

- **Why:** no predicted column matches gold column 'stores'
- **Tables:** store, store_returns, store_sales, date_dim

```sql
SELECT
  COUNT(DISTINCT s_store_sk) AS total_stores
FROM store
LIMIT 501
```

### m06 (medium): Which household income band generated the most in-store revenue? Show its lower and upper bound.

- **Why:** 2 columns < gold's 3
- **Tables:** store_sales, customer, household_demographics, income_band, date_dim

```sql
SELECT
  ib.ib_lower_bound AS lower_bound,
  ib.ib_upper_bound AS upper_bound
FROM store_sales AS ss
JOIN household_demographics AS hd
  ON ss.ss_hdemo_sk = hd.hd_demo_sk
JOIN income_band AS ib
  ON hd.hd_income_band_sk = ib.ib_income_band_sk
GROUP BY
  ib.ib_lower_bound,
  ib.ib_upper_bound
ORDER BY
  SUM(ss.ss_net_paid) DESC
LIMIT 1
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
