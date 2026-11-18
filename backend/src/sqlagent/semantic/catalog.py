"""Semantic catalog: business-friendly description of the warehouse.

Built offline by `sqlagent build-catalog` (curated YAML + live introspection) and loaded at
runtime from JSON, so serving a request never needs to introspect the warehouse.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, Field


class Column(BaseModel):
    name: str
    type: str
    description: str = ""
    sample_values: list[str] = Field(default_factory=list)
    distinct_values_complete: bool = False  # True -> sample_values is the full domain
    references: str | None = None  # "table.column" this FK points to


class Table(BaseModel):
    name: str
    kind: str  # "fact" | "dimension"
    business_name: str
    description: str
    synonyms: list[str] = Field(default_factory=list)
    row_count: int = 0
    columns: list[Column]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Coverage(BaseModel):
    date_column: str
    min_date: str
    max_date: str


class Catalog(BaseModel):
    dialect: str = "duckdb"
    domain: str
    rules: list[str]
    metrics: dict[str, str]
    coverage: dict[str, Coverage] = Field(default_factory=dict)
    tables: list[Table]

    # --- loading -----------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> Catalog:
        return cls.model_validate(json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=1, exclude_defaults=True) + "\n")

    # --- lookups -----------------------------------------------------------------------
    @cached_property
    def by_name(self) -> dict[str, Table]:
        return {t.name: t for t in self.tables}

    @cached_property
    def edges(self) -> list[tuple[str, str, str, str]]:
        """FK edges as (from_table, from_column, to_table, to_column)."""
        out = []
        for t in self.tables:
            for c in t.columns:
                if c.references:
                    to_t, to_c = c.references.split(".", 1)
                    out.append((t.name, c.name, to_t, to_c))
        return out

    @cached_property
    def neighbors(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {t.name: set() for t in self.tables}
        for a, _, b, _ in self.edges:
            adj[a].add(b)
            adj[b].add(a)
        return adj

    # --- prompt rendering --------------------------------------------------------------
    def render_overview(self) -> str:
        """One line per table: enough for routing/ambiguity detection, ~1k tokens."""
        lines = [f"Domain: {self.domain.strip()}", "", "Tables:"]
        for t in self.tables:
            lines.append(f"- {t.name} ({t.kind}, {t.business_name}): {t.description}")
        lines += ["", "Data coverage (relative dates are anchored to these, not today):"]
        lines += [f"- {name}: {c.min_date} to {c.max_date}" for name, c in self.coverage.items()]
        return "\n".join(lines)

    def render_tables(self, names: list[str]) -> str:
        """Detailed schema slice for the SQL writer: columns, types, meanings, values."""
        # Token budget matters: free LLM tiers allow ~8k tokens/minute per model. Two
        # lossless compressions: foreign keys render as a compact arrow (their join condition
        # is also listed under JOIN PATHS), and a long description repeated across channel
        # tables (ss_/cs_/ws_ share column semantics) is printed once, then referenced.
        selected = set(names)
        blocks = []
        first_with_desc: dict[str, str] = {}
        for name in names:
            t = self.by_name[name]
            blocks.append(f"TABLE {t.name}  -- {t.business_name}: {t.description} (~{t.row_count:,} rows)")
            for c in t.columns:
                if c.references:
                    to_table = c.references.split(".")[0]
                    role = c.description.split(":")[0].lower()
                    hidden = "" if to_table in selected else ", table not shown"
                    blocks.append(f"  {c.name} -> {c.references} ({role}{hidden})")
                    continue
                note = c.description
                if len(note.split()) >= 6:
                    if note in first_with_desc:
                        note = f"same meaning as {first_with_desc[note]}"
                    else:
                        first_with_desc[note] = c.name
                if c.sample_values:
                    vals = ", ".join(repr(v) for v in c.sample_values)
                    note += f" | {'values' if c.distinct_values_complete else 'e.g.'}: {vals}"
                blocks.append(f"  {c.name} {c.type}  -- {note}")
            blocks.append("")

        joins = [f"{a}.{ac} = {b}.{bc}" for a, ac, b, bc in self.edges if a in selected and b in selected]
        if joins:
            blocks.append("JOIN PATHS between the tables above:")
            blocks += [f"  {j}" for j in joins]
            blocks.append("")

        cov = [f"  {n}: {c.min_date} to {c.max_date}" for n, c in self.coverage.items() if n in selected]
        if cov:
            blocks.append("DATA COVERAGE (anchor relative dates here):")
            blocks += cov
            blocks.append("")

        blocks.append("BUSINESS METRICS ({p} = fact column prefix ss/cs/ws):")
        blocks += [f"  {k}: {v}" for k, v in self.metrics.items()]
        blocks.append("")
        blocks.append("RULES:")
        blocks += [f"  - {r}" for r in self.rules]
        return "\n".join(blocks)


def table_prefix(columns: list[str]) -> str:
    """Longest common underscore-terminated prefix, e.g. ['ss_a','ss_b'] -> 'ss_'."""
    first = columns[0]
    prefix = ""
    for i, ch in enumerate(first):
        if ch == "_" and all(c.startswith(first[: i + 1]) for c in columns):
            prefix = first[: i + 1]
    return prefix
