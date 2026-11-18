"""Render the column reference of docs/data-dictionary.md from the semantic catalog.

Only the region between the GENERATED markers is rewritten, so the hand-written prose
around it stays editable and the column tables can never drift from what the agents see.
"""

from __future__ import annotations

from pathlib import Path

from sqlagent.semantic.catalog import Catalog, Table

BEGIN = "<!-- BEGIN GENERATED: run `uv run sqlagent docs` in backend/ to refresh -->"
END = "<!-- END GENERATED -->"

GROUPS = [
    ("Sales facts", ["store_sales", "catalog_sales", "web_sales"]),
    ("Returns facts", ["store_returns", "catalog_returns", "web_returns"]),
    ("Inventory fact", ["inventory"]),
    ("Time dimensions", ["date_dim", "time_dim"]),
    ("Product dimension", ["item"]),
    (
        "Customer dimensions",
        ["customer", "customer_address", "customer_demographics", "household_demographics", "income_band"],
    ),
    (
        "Channel and location dimensions",
        ["store", "warehouse", "call_center", "catalog_page", "web_page", "web_site", "ship_mode"],
    ),
    ("Marketing and returns dimensions", ["promotion", "reason"]),
]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _values(col) -> str:
    if not col.sample_values:
        return ""
    vals = ", ".join(f"`{_cell(v)}`" for v in col.sample_values[:12])
    if col.distinct_values_complete:
        more = len(col.sample_values) > 12
        return vals + (", …" if more else "")
    return "e.g. " + vals


def render_table(t: Table, catalog: Catalog) -> str:
    lines = [f"### `{t.name}`: {t.business_name}", ""]
    lines.append(f"{t.description}  ")
    lines.append(f"**Type:** {t.kind} · **Rows (SF1):** {t.row_count:,} · **Columns:** {len(t.columns)}")
    if t.synonyms:
        lines.append(f"  \n**Business terms:** {', '.join(t.synonyms)}")
    if cov := catalog.coverage.get(t.name):
        lines.append(f"  \n**Data covers:** {cov.min_date} to {cov.max_date} (by `{cov.date_column}`)")
    lines += ["", "| Column | Type | Meaning | Links to | Values |", "|---|---|---|---|---|"]
    for c in t.columns:
        link = f"`{c.references}`" if c.references else ""
        lines.append(f"| `{c.name}` | {c.type} | {_cell(c.description)} | {link} | {_values(c)} |")
    return "\n".join(lines)


def render_reference(catalog: Catalog) -> str:
    seen: set[str] = set()
    out = []
    for title, names in GROUPS:
        out.append(f"## {title}\n")
        for n in names:
            out.append(render_table(catalog.by_name[n], catalog) + "\n")
            seen.add(n)
    missing = [t.name for t in catalog.tables if t.name not in seen]
    if missing:
        raise ValueError(f"Tables not assigned to a docs group: {missing}")
    return "\n".join(out)


def write_reference(catalog: Catalog, path: Path) -> None:
    text = path.read_text()
    if BEGIN not in text or END not in text:
        raise ValueError(f"{path} is missing the GENERATED markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    path.write_text(f"{head}{BEGIN}\n\n{render_reference(catalog)}\n{END}{tail}")
