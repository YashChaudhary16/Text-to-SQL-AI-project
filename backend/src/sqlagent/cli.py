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
        async for mode, chunk in graph.astream(
            graph_input, config, context=RunContext(), stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                if chunk["type"] == "waiting":
                    print(f"[waiting] free-tier rate limit: retrying in {chunk['seconds']}s")
                else:
                    print(f"[fallback] {chunk['model']}: {chunk['reason']}, trying the next model")
                continue
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


def run_evaluation(args) -> None:
    import asyncio
    import datetime as dt
    import subprocess

    from sqlagent.evals.report import write_reports
    from sqlagent.evals.runner import check_gold, load_cases, run_eval

    overrides: dict[str, object] = {"max_rate_limit_wait_s": args.max_wait}
    if args.router_models:
        overrides["router_models"] = args.router_models
    if args.sql_models:
        overrides["sql_models"] = args.sql_models
    settings = get_settings().model_copy(update=overrides)

    cases = load_cases(args.gold)
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c.id in wanted]
    if args.category:
        cases = [c for c in cases if c.category == args.category]
    if args.check_gold:
        raise SystemExit(0 if check_gold(settings, cases) else 1)

    first_sql = settings.sql_models.split(",")[0].split(":", 1)[-1].replace("/", "-").replace(":", "-")
    name = args.name or f"{dt.date.today().isoformat()}-{first_sql}"
    run_dir = BACKEND_DIR / "evals" / "runs" / name
    if args.rescore:
        from sqlagent.evals.runner import rescore

        results = rescore(settings, load_cases(args.gold), run_dir / "results.jsonl")
    else:
        results = asyncio.run(run_eval(settings, cases, run_dir, pace_s=args.pace))
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=BACKEND_DIR
        ).stdout.strip()
    except OSError:
        commit = "?"
    meta = {
        "name": name,
        "date": dt.datetime.now().isoformat(timespec="minutes"),
        "commit": commit,
        "gold_set": args.gold.name,
        "router_models": settings.router_models,
        "sql_models": settings.sql_models,
    }
    summary = write_reports(run_dir, results, meta)
    if summary["infra_failures"]:
        print(
            f"\nINCOMPLETE: {summary['infra_failures']} of {summary['cases_total']} cases not scored; "
            "numbers below are partial. Resume with the same --name."
        )
    acc = summary["execution_accuracy"]
    print(f"\nExecution accuracy: {acc:.1%}" if acc is not None else "\nNo data cases scored")
    print(f"Report: {run_dir / 'report.md'}")


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

    ev = sub.add_parser("eval", help="Run the gold evaluation set and write a report")
    ev.add_argument("--name", help="run name (default: <date>-<sql model>); reuse a name to resume")
    ev.add_argument("--gold", type=Path, default=BACKEND_DIR / "evals" / "tpcds_gold.yaml")
    ev.add_argument("--ids", help="comma-separated case ids to run")
    ev.add_argument("--category", help="only run cases in this category")
    ev.add_argument("--router-models", help="override ROUTER_MODELS for this run")
    ev.add_argument("--sql-models", help="override SQL_MODELS for this run")
    ev.add_argument("--pace", type=float, default=0.0, help="seconds to sleep between cases (rate limits)")
    ev.add_argument(
        "--max-wait",
        type=float,
        default=90.0,
        help="max seconds to wait out a rate limit (batch evals can wait longer than interactive use)",
    )
    ev.add_argument("--check-gold", action="store_true", help="only validate the gold queries")
    ev.add_argument(
        "--rescore", action="store_true", help="re-score stored SQL against current gold (no LLM)"
    )

    ask_p = sub.add_parser("ask", help="Ask a question end-to-end in the terminal")
    ask_p.add_argument("question")

    args = parser.parse_args()
    if args.cmd == "eval":
        logging.getLogger().setLevel(logging.ERROR)
        run_evaluation(args)
        return
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
