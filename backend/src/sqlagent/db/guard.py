"""Static safety checks for LLM-generated SQL.

Defence in depth: the warehouse connection is also opened read-only with external access
disabled, but we reject anything that isn't a single read-only query over known tables
before it ever reaches the database.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Statement / expression types that must never appear anywhere in the tree.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # PRAGMA, SET, ATTACH, COPY, INSTALL, LOAD ... parse as Command
    exp.Copy,
    exp.Pragma,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

# Table functions / functions that read outside the warehouse or leak environment info.
FORBIDDEN_FUNCTION_PREFIXES = ("read_", "glob", "parquet_", "sniff_", "duckdb_", "pragma_")
FORBIDDEN_FUNCTIONS = {"getenv", "current_setting", "query", "query_table", "sql_auto_complete"}


class UnsafeSQLError(ValueError):
    """Raised when generated SQL fails a safety check."""


@dataclass(frozen=True)
class GuardedSQL:
    sql: str  # normalized SQL that will actually run
    tables: list[str]
    limited: bool  # True if we injected/clamped a LIMIT


def guard_sql(sql: str, allowed_tables: set[str], max_rows: int, dialect: str = "duckdb") -> GuardedSQL:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except ParseError as e:
        raise UnsafeSQLError(f"SQL does not parse: {e.errors[0]['description'] if e.errors else e}") from e
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError(f"Only SELECT queries are allowed (got {tree.key.upper()}).")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"Forbidden operation: {node.key.upper()}.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise UnsafeSQLError(f"Function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    referenced = []
    for table in tree.find_all(exp.Table):
        if not table.name:  # table-valued function, e.g. FROM range(10)
            raise UnsafeSQLError("Table functions are not allowed.")
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError(f"Qualified table names are not allowed: {table.sql(dialect)}")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in allowed_tables:
            raise UnsafeSQLError(f"Unknown table: {table.name}")
        referenced.append(name)

    limited = False
    limit = tree.args.get("limit")
    current = _literal_int(limit.expression) if isinstance(limit, exp.Limit) else None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows + 1)  # +1 lets the executor detect truncation
        limited = True

    return GuardedSQL(
        sql=tree.sql(dialect=dialect, pretty=True), tables=sorted(set(referenced)), limited=limited
    )


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None
