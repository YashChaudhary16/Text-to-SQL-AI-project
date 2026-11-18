# Evaluation report: 2026-11-17-ollama-prefix-dev

- **Date:** 2026-11-18T12:16 · **Git commit:** `2ccce55` · **Gold set:** tpcds_gold.yaml
- **Router models:** `ollama:gpt-oss:20b`
- **SQL models:** `ollama:gpt-oss:20b`

## Headline

| Metric | Value |
|---|---|
| **Execution accuracy** (data questions, relaxed) | **71.4%** (25/35) |
| Execution accuracy, strict (no extra columns) | 57.1% |
| Routing accuracy (all cases) | 100.0% |
| Cost per question | $0.0000 |
| Median / p90 latency | 55.46 s / 99.56 s |
| Tokens per question (in / out) | 4,208 / 1,218 |
| SQL retries: needed / rescued | 2 / 1 |
| Clarifying questions asked | 3 |
| Provider fallbacks / rate-limit waits | 0 / 0 |
| Cases not scored (all providers unavailable) | 0 |

## By category

| Category | Accuracy |
|---|---|
| easy | 90.0% (9/10) |
| medium | 69.2% (9/13) |
| hard | 50.0% (5/10) |
| ambiguous | 100.0% (2/2) |
| schema | 100.0% (3/3) |
| out_of_scope | 100.0% (3/3) |

## Model calls

| Model | Calls |
|---|---|
| `ollama:gpt-oss:20b` | 84 |

## Failures (10)

### e07 (easy): How many of our customers are preferred customers?

- **Why:** no predicted column matches gold column 'preferred'
- **Tables:** customer, customer_demographics

```sql
SELECT
  COUNT(DISTINCT c.c_customer_sk) AS preferred_customer_count
FROM customer AS c
JOIN customer_demographics AS cd
  ON c.c_current_cdemo_sk = cd.cd_demo_sk
WHERE
  cd.cd_credit_rating = 'Preferred'
LIMIT 501
```

### h01 (hard): What is the store return rate (units returned divided by units sold) for each product category?

- **Why:** row count 11 != gold 10
- **Tables:** store_returns, store_sales, item, catalog_returns, date_dim

```sql
WITH sales AS (
  SELECT
    i.i_category AS category,
    COUNT(*) AS units_sold
  FROM store_sales AS ss
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  GROUP BY
    i.i_category
), returns AS (
  SELECT
    i.i_category AS category,
    COUNT(*) AS units_returned
  FROM store_returns AS sr
  JOIN item AS i
    ON sr.sr_item_sk = i.i_item_sk
  GROUP BY
    i.i_category
)
SELECT
  s.category,
  s.units_sold,
  COALESCE(r.units_returned, 0) AS units_returned,
  ROUND(CAST(COALESCE(r.units_returned, 0) AS DECIMAL(18, 3)) / NULLIF(s.units_sold, 0), 6) AS return_rate
FROM sales AS s
LEFT JOIN returns AS r
  ON s.category = r.category
ORDER BY
  return_rate DESC
LIMIT 500
```

### h04 (hard): Who are the top 10 customers by total revenue across all channels? Show their customer IDs.

- **Why:** 1 columns < gold's 2
- **Tables:** store_sales, catalog_sales, web_sales, customer, date_dim

```sql
WITH store_rev AS (
  SELECT
    ss_customer_sk AS customer_sk,
    SUM(ss_net_paid) AS rev
  FROM store_sales
  GROUP BY
    ss_customer_sk
), catalog_rev AS (
  SELECT
    cs_bill_customer_sk AS customer_sk,
    SUM(cs_net_paid) AS rev
  FROM catalog_sales
  GROUP BY
    cs_bill_customer_sk
), web_rev AS (
  SELECT
    ws_bill_customer_sk AS customer_sk,
    SUM(ws_net_paid) AS rev
  FROM web_sales
  GROUP BY
    ws_bill_customer_sk
), combined AS (
  SELECT
    customer_sk,
    SUM(rev) AS total_rev
  FROM (
    SELECT
      customer_sk,
      rev
    FROM store_rev
    UNION ALL
    SELECT
      customer_sk,
      rev
    FROM catalog_rev
    UNION ALL
    SELECT
      customer_sk,
      rev
    FROM web_rev
  ) AS t
  GROUP BY
    customer_sk
)
SELECT
  cust.c_customer_id AS customer_id
FROM combined AS cc
JOIN customer AS cust
  ON cc.customer_sk = cust.c_customer_sk
ORDER BY
  cc.total_rev DESC
LIMIT 10
```

### h07 (hard): For items managed by manager 1, what were in-store gross sales by category in November 2000?

- **Why:** row count 11 != gold 10
- **Tables:** store_sales, store, item, date_dim, catalog_sales

```sql
SELECT
  i.i_category AS product_category,
  ROUND(SUM(ss.ss_net_paid), 2) AS total_net_paid
FROM store_sales AS ss
JOIN date_dim AS d
  ON ss.ss_sold_date_sk = d.d_date_sk
JOIN item AS i
  ON ss.ss_item_sk = i.i_item_sk
JOIN store AS s
  ON ss.ss_store_sk = s.s_store_sk
WHERE
  d.d_year = 2000 AND d.d_moy = 11 AND s.s_manager = 'Edwin Adams'
GROUP BY
  i.i_category
ORDER BY
  total_net_paid DESC
LIMIT 500
```

### h08 (hard): For manager 28's items, which brands had the highest in-store gross sales in November 1999? Show brand ID, brand and sales for the top 100.

- **Why:** no predicted column matches gold column 'i_brand_id'
- **Tables:** store_sales, date_dim, store, item

```sql
SELECT
  brand_id,
  brand_name,
  ROUND(net_paid_total, 2) AS net_paid_total
FROM (
  SELECT
    i.i_brand_id AS brand_id,
    i.i_brand AS brand_name,
    SUM(ss.ss_net_paid) AS net_paid_total
  FROM store_sales AS ss
  JOIN date_dim AS dd
    ON ss.ss_sold_date_sk = dd.d_date_sk
  JOIN item AS i
    ON ss.ss_item_sk = i.i_item_sk
  WHERE
    dd.d_year = 1999 AND dd.d_moy = 11
  GROUP BY
    i.i_brand_id,
    i.i_brand
) AS brand_sales
ORDER BY
  net_paid_total DESC
LIMIT 100
```

### h09 (hard): For single, college-educated men in 2000, excluding purchases where the promotion ran by both email and event, what were the average quantity, list price, coupon amount and sales price per product ID in store sales? Show the first 100 product IDs alphabetically.

- **Why:** row count 0 != gold 100
- **Tables:** store_sales, date_dim, customer, customer_demographics, promotion, item, catalog_sales

```sql
SELECT
  i.i_item_id AS product_id,
  ROUND(AVG(ss.ss_quantity), 2) AS avg_quantity,
  ROUND(AVG(ss.ss_list_price), 2) AS avg_list_price,
  ROUND(AVG(ss.ss_coupon_amt), 2) AS avg_coupon_amount,
  ROUND(AVG(ss.ss_net_paid), 2) AS avg_sales_price
FROM store_sales AS ss
JOIN date_dim AS d
  ON ss.ss_sold_date_sk = d.d_date_sk
JOIN customer_demographics AS cd
  ON ss.ss_cdemo_sk = cd.cd_demo_sk
LEFT JOIN promotion AS p
  ON ss.ss_promo_sk = p.p_promo_sk
JOIN item AS i
  ON ss.ss_item_sk = i.i_item_sk
WHERE
  d.d_year = 2000
  AND cd.cd_gender = 'M'
  AND cd.cd_marital_status = 'Single'
  AND cd.cd_education_status = 'College'
  AND NOT (
    COALESCE(p.p_channel_email, 'N') = 'Y' AND COALESCE(p.p_channel_event, 'N') = 'Y'
  )
GROUP BY
  i.i_item_id
ORDER BY
  i.i_item_id ASC
LIMIT 100
```

### m01 (medium): How many stores are there?

- **Why:** no predicted column matches gold column 'stores'
- **Tables:** store, store_returns, store_sales, date_dim

```sql
SELECT
  COUNT(*) AS store_count
FROM store
LIMIT 501
```

### m04 (medium): What are the top 5 return reasons by total return amount across all channels?

- **Why:** no predicted column matches gold column 'r_reason_desc'
- **Tables:** store_returns, catalog_returns, web_returns, reason, date_dim

```sql
WITH reason_returns AS (
  SELECT
    r.r_reason_desc AS reason_desc,
    sr.sr_net_loss AS net_return
  FROM store_returns AS sr
  JOIN reason AS r
    ON sr.sr_reason_sk = r.r_reason_sk
  UNION ALL
  SELECT
    r.r_reason_desc,
    cr.cr_net_loss
  FROM catalog_returns AS cr
  JOIN reason AS r
    ON cr.cr_reason_sk = r.r_reason_sk
  UNION ALL
  SELECT
    r.r_reason_desc,
    wr.wr_net_loss
  FROM web_returns AS wr
  JOIN reason AS r
    ON wr.wr_reason_sk = r.r_reason_sk
)
SELECT
  reason_desc,
  ROUND(SUM(net_return), 2) AS total_net_return
FROM reason_returns
GROUP BY
  reason_desc
ORDER BY
  total_net_return DESC
LIMIT 5
```

### m05 (medium): What was in-store revenue in 2000 from married women with a college degree?

- **Why:** no predicted column matches gold column 'revenue'
- **Tables:** store_sales, date_dim, customer_demographics

```sql
SELECT
  ROUND(SUM(ss_net_paid), 2) AS in_store_revenue
FROM store_sales AS ss
JOIN date_dim AS d
  ON ss.ss_sold_date_sk = d.d_date_sk
JOIN customer_demographics AS cd
  ON ss.ss_cdemo_sk = cd.cd_demo_sk
WHERE
  d.d_year = 2000
  AND cd.cd_marital_status = 'M'
  AND cd.cd_gender = 'F'
  AND cd.cd_education_status IN ('College', 'Advanced Degree', '4 yr Degree')
LIMIT 501
```

### m06 (medium): Which household income band generated the most in-store revenue? Show its lower and upper bound.

- **Why:** no predicted column matches gold column 'ib_lower_bound'
- **Tables:** store_sales, customer, household_demographics, income_band, date_dim

```sql
SELECT
  lower_bound,
  upper_bound,
  ROUND(SUM(net_paid), 2) AS total_revenue
FROM (
  SELECT
    ib.ib_lower_bound AS lower_bound,
    ib.ib_upper_bound AS upper_bound,
    ss.ss_net_paid AS net_paid
  FROM store_sales AS ss
  JOIN customer AS cu
    ON ss.ss_customer_sk = cu.c_customer_sk
  JOIN household_demographics AS hd
    ON cu.c_current_hdemo_sk = hd.hd_demo_sk
  JOIN income_band AS ib
    ON hd.hd_income_band_sk = ib.ib_income_band_sk
) AS t
GROUP BY
  lower_bound,
  upper_bound
ORDER BY
  total_revenue DESC
LIMIT 1
```

## Method

Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,
self-correction) with the final summary step skipped. If the agent asks a clarifying
question, the case's scripted reply is sent. The predicted result is compared with the
gold query's result: every gold column must match a distinct predicted column (extra columns
allowed), rows must line up, numbers within 0.01, and order is checked only where the question
implies it. Strict accuracy also requires the same number of columns.
