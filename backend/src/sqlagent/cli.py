"""Command-line entry points: `uv run sqlagent <command>`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb

from sqlagent.config import BACKEND_DIR, get_settings


def generate_data(sf: float, out: Path) -> None:
    """Generate TPC-DS at scale factor `sf` into a DuckDB file (SF1 ~ 19M rows, ~315 MB)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"{out} already exists; delete it first to regenerate.")
    con = duckdb.connect(str(out))
    con.execute("INSTALL tpcds; LOAD tpcds;")
    con.execute(f"CALL dsdgen(sf={sf})")
    con.close()
    print(f"Wrote {out} ({out.stat().st_size / 1e6:,.0f} MB)")


def build_catalog() -> None:
    from sqlagent.semantic.builder import build_catalog as build

    s = get_settings()
    con = duckdb.connect(s.warehouse_url, read_only=not s.warehouse_url.startswith("md:"))
    catalog = build(con, s.semantic_path)
    catalog.save(s.catalog_path)
    n_cols = sum(len(t.columns) for t in catalog.tables)
    print(f"Wrote {s.catalog_path}: {len(catalog.tables)} tables, {n_cols} columns")


async def ask(question: str) -> None:
    """Run one question through the full graph, answering clarifications interactively."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from sqlagent.db.warehouse import Warehouse
    from sqlagent.graph.agent import Deps, build_graph
    from sqlagent.graph.state import RunContext, new_turn
    from sqlagent.llm import LLMRouter
    from sqlagent.retrieval import SchemaRetriever
    from sqlagent.semantic.catalog import Catalog

    s = get_settings()
    catalog = Catalog.load(s.catalog_path)
    deps = Deps(
        settings=s,
        catalog=catalog,
        retriever=SchemaRetriever(catalog, s.retrieval_top_tables),
        warehouse=Warehouse(s, {t.name for t in catalog.tables}),
        llm=LLMRouter(s),
    )
    graph = build_graph(deps, InMemorySaver())
    config = {"configurable": {"thread_id": "cli"}}
    graph_input: object = new_turn(question)
    while True:
        async for chunk in graph.astream(graph_input, config, context=RunContext(), stream_mode="updates"):
            for node, u in chunk.items():
                if node == "__interrupt__":
                    continue
                u = u or {}
                model = (u.get("llm_calls") or [{}])[-1].get("model", "")
                if node == "understand":
                    print(f"[understand] {u.get('intent')} | {u.get('standalone_question')}  ({model})")
                    for a in u.get("assumptions", []):
                        print(f"    assumption: {a}")
                elif node == "retrieve":
                    print(f"[retrieve] tables: {', '.join(u.get('tables', []))}")
                elif node == "write_sql":
                    print(f"[write_sql] ({model})\n{u.get('sql')}")
                elif node == "execute":
                    r = u.get("result")
                    if r:
                        print(f"[execute] {r['row_count']} rows in {r['elapsed_ms']} ms")
                        print("    " + " | ".join(r["columns"]))
                        for row in r["rows"][:10]:
                            print("    " + " | ".join(str(v) for v in row))
                    else:
                        print(f"[execute] attempt {u.get('attempts')} failed: {u.get('last_error')}")
                elif u.get("answer"):
                    print(f"\n{u['answer']}")
        state = await graph.aget_state(config)
        if not state.interrupts:
            break
        pending = state.interrupts[0].value
        print(f"\n[clarify] {pending['question']}")
        for i, opt in enumerate(pending.get("options", []), 1):
            print(f"  {i}. {opt}")
        reply = input("> ").strip()
        opts = pending.get("options", [])
        if reply.isdigit() and 1 <= int(reply) <= len(opts):
            reply = opts[int(reply) - 1]
        graph_input = Command(resume=reply)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="sqlagent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate-data", help="Generate TPC-DS into a local DuckDB file")
    gen.add_argument("--sf", type=float, default=1)
    gen.add_argument("--out", type=Path, default=None)

    sub.add_parser("build-catalog", help="Build semantic/tpcds.catalog.json from the warehouse")
    sub.add_parser("docs", help="Regenerate the column reference in docs/data-dictionary.md")

    serve = sub.add_parser("serve", help="Run the API locally")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    ask_p = sub.add_parser("ask", help="Ask a question end-to-end in the terminal")
    ask_p.add_argument("question")

    args = parser.parse_args()
    if args.cmd == "ask":
        import asyncio

        logging.getLogger().setLevel(logging.WARNING)
        asyncio.run(ask(args.question))
        return
    if args.cmd == "generate-data":
        sf_label = f"{args.sf:g}".replace(".", "_")
        generate_data(args.sf, args.out or BACKEND_DIR / "data" / f"tpcds_sf{sf_label}.duckdb")
    elif args.cmd == "build-catalog":
        build_catalog()
    elif args.cmd == "docs":
        from sqlagent.semantic.catalog import Catalog
        from sqlagent.semantic.docs import write_reference

        path = BACKEND_DIR.parent / "docs" / "data-dictionary.md"
        write_reference(Catalog.load(get_settings().catalog_path), path)
        print(f"Updated {path}")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("sqlagent.api.app:app", port=args.port, reload=args.reload)
