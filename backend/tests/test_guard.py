import pytest

from sqlagent.db.guard import UnsafeSQLError, guard_sql

TABLES = {"store_sales", "date_dim", "item"}


def g(sql: str, max_rows: int = 100):
    return guard_sql(sql, TABLES, max_rows)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM store_sales",
        "DROP TABLE item",
        "UPDATE item SET i_brand = 'x'",
        "INSERT INTO item SELECT * FROM item",
        "CREATE TABLE t AS SELECT 1",
        "ATTACH 'other.db'",
        "PRAGMA database_list",
        "COPY item TO 'out.csv'",
        "SET threads = 1",
        "INSTALL httpfs",
        "SELECT 1; DROP TABLE item",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
        "SELECT getenv('HOME')",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM customer",  # not in allow-list
        "SELECT * FROM other_db.main.item",
        "WITH x AS (DELETE FROM item RETURNING *) SELECT * FROM x",
        "",
    ],
)
def test_rejects_unsafe(sql):
    with pytest.raises(UnsafeSQLError):
        g(sql)


def test_allows_select_with_cte_and_joins():
    out = g(
        """WITH yearly AS (
             SELECT d_year, SUM(ss_net_paid) AS revenue
             FROM store_sales JOIN date_dim ON ss_sold_date_sk = d_date_sk
             GROUP BY d_year)
           SELECT * FROM yearly ORDER BY d_year LIMIT 10"""
    )
    assert out.tables == ["date_dim", "store_sales"]
    assert not out.limited
    assert "LIMIT 10" in out.sql


def test_injects_limit_when_missing_or_too_large():
    assert g("SELECT i_brand FROM item").limited
    assert "LIMIT 101" in g("SELECT i_brand FROM item").sql
    assert "LIMIT 101" in g("SELECT i_brand FROM item LIMIT 100000").sql


def test_union_is_allowed():
    out = g("SELECT i_brand FROM item UNION ALL SELECT i_brand FROM item")
    assert out.tables == ["item"]
