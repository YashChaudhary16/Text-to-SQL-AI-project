"""Build the semantic catalog from curated YAML + live warehouse introspection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import yaml

from sqlagent.semantic.catalog import Catalog, Column, Coverage, Table, table_prefix

log = logging.getLogger(__name__)

LOW_CARDINALITY = 40  # list the full value domain at or below this many distinct values
EXAMPLES = 3  # otherwise show this many example values
SKIP_SAMPLES = re.compile(r"(_sk|_id|_desc|_date|_url|_email_address|_login)$")


def humanize(suffix: str, abbreviations: dict[str, str]) -> str:
    words = [abbreviations.get(tok, tok) for tok in suffix.split("_") if tok]
    return " ".join(words)


def resolve_fk(suffix: str, dimension_keys: dict[str, str]) -> str | None:
    for key in sorted(dimension_keys, key=len, reverse=True):
        if suffix == key or suffix.endswith("_" + key):
            return dimension_keys[key]
    return None


def describe_column(
    table: str,
    column: str,
    prefix: str,
    spec: dict,
    overrides: dict[str, str],
) -> tuple[str, str | None]:
    """Return (description, references) for a column."""
    suffix = column[len(prefix) :]
    ref = resolve_fk(suffix, spec["dimension_keys"])
    if ref and ref.split(".")[0] == table:
        ref = None  # the dimension's own primary key

    if column in overrides:
        desc = overrides[column]
    elif suffix in spec["column_patterns"]:
        desc = spec["column_patterns"][suffix]
    elif ref:
        role = suffix.rsplit("_sk", 1)[0]
        desc = f"{humanize(role, spec['abbreviations'])} (join key)"
    elif suffix.endswith("_sk"):
        desc = "Primary key (surrogate)."
    else:
        desc = humanize(suffix, spec["abbreviations"]).capitalize() + "."
    return desc, ref


def build_catalog(con: duckdb.DuckDBPyConnection, semantic_path: Path) -> Catalog:
    spec = yaml.safe_load(semantic_path.read_text())
    curated: dict[str, dict] = spec["tables"]

    columns_by_table: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in con.execute(
        """select table_name, column_name, data_type from duckdb_columns()
           where schema_name = 'main' and database_name = current_database()
           order by table_name, column_index"""
    ).fetchall():
        columns_by_table.setdefault(table, []).append((column, dtype))

    missing = set(curated) - set(columns_by_table)
    if missing:
        raise ValueError(f"Curated tables missing from warehouse: {sorted(missing)}")

    tables: list[Table] = []
    for name, info in curated.items():
        cols = columns_by_table[name]
        prefix = table_prefix([c for c, _ in cols])
        row_count = con.execute(f'select count(*) from "{name}"').fetchone()[0]
        out_cols = []
        for column, dtype in cols:
            desc, ref = describe_column(name, column, prefix, spec, info.get("columns") or {})
            col = Column(name=column, type=dtype, description=desc, references=ref)
            if dtype.startswith("VARCHAR") and not SKIP_SAMPLES.search(column):
                _add_samples(con, name, col)
            out_cols.append(col)
        tables.append(
            Table(
                name=name,
                kind=info["kind"],
                business_name=info["business_name"],
                description=info["description"],
                synonyms=info.get("synonyms", []),
                row_count=row_count,
                columns=out_cols,
            )
        )
        log.info("catalogued %s (%s rows)", name, f"{row_count:,}")

    catalog = Catalog(
        domain=spec["domain"],
        rules=spec["rules"],
        metrics=spec["metrics"],
        tables=tables,
    )
    catalog.coverage = _coverage(con, catalog)
    return catalog


def _add_samples(con: duckdb.DuckDBPyConnection, table: str, col: Column) -> None:
    q = f'"{col.name}"'
    n = con.execute(f'select approx_count_distinct({q}) from "{table}"').fetchone()[0]
    if n == 0:
        return
    limit = LOW_CARDINALITY + 1 if n <= LOW_CARDINALITY else EXAMPLES
    rows = con.execute(
        f"select {q}, count(*) c from \"{table}\" where {q} is not null and trim({q}) <> '' "
        f"group by 1 order by c desc, 1 limit {limit}"
    ).fetchall()
    col.sample_values = [str(r[0]).strip() for r in rows][: max(LOW_CARDINALITY, EXAMPLES)]
    col.distinct_values_complete = len(rows) <= LOW_CARDINALITY and n <= LOW_CARDINALITY


def _coverage(con: duckdb.DuckDBPyConnection, catalog: Catalog) -> dict[str, Coverage]:
    out = {}
    for t in catalog.tables:
        if t.kind != "fact":
            continue
        date_col = next((c.name for c in t.columns if c.references == "date_dim.d_date_sk"), None)
        if not date_col:
            continue
        lo, hi = con.execute(
            f'select min(d_date), max(d_date) from "{t.name}" join date_dim on {date_col} = d_date_sk'
        ).fetchone()
        out[t.name] = Coverage(date_column=date_col, min_date=str(lo), max_date=str(hi))
    return out
