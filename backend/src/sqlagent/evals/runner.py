"""Run the gold set through the real agent graph and score each case.

Results are appended to `<run_dir>/results.jsonl` after every case, so a run interrupted by
rate limits or a daily quota can be resumed: completed cases are skipped, and cases that
failed for infrastructure reasons (every LLM provider unavailable) are retried.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sqlagent.config import Settings
from sqlagent.db.warehouse import Warehouse
from sqlagent.evals.compare import compare
from sqlagent.graph.agent import Deps, build_graph
from sqlagent.graph.state import RunContext, new_turn
from sqlagent.llm import AllProvidersFailedError, LLMRouter
from sqlagent.retrieval import SchemaRetriever
from sqlagent.semantic.catalog import Catalog

DEFAULT_CLARIFICATION = "Use sensible defaults and state your assumptions."
MAX_RESUMES = 2
MAX_CONSECUTIVE_INFRA = 3  # stop instead of burning through cases on an exhausted quota


@dataclass
class Case:
    id: str
    category: str
    question: str
    gold_sql: str | None = None
    expected_intent: str | None = None
    order_matters: bool = False
    clarification_answer: str | None = None
    source: str | None = None
    notes: str | None = None

    @property
    def intent(self) -> str:
        return self.expected_intent or "data_question"


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    expected_intent: str
    intent: str | None = None
    route_ok: bool = False
    clarifications: list[dict[str, str]] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    sql: str | None = None
    attempts: int = 0
    executed: bool = False
    match: bool | None = None  # None for routing-only cases
    strict: bool | None = None
    reason: str = ""
    error: str | None = None
    infra_error: bool = False
    latency_s: float = 0.0
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return self.route_ok and (self.match is not False)


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text())["cases"]
    ids = [c["id"] for c in raw]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids in gold set")
    return [Case(**c) for c in raw]


def build_eval_deps(settings: Settings) -> Deps:
    catalog = Catalog.load(settings.catalog_path)
    return Deps(
        settings=settings,
        catalog=catalog,
        retriever=SchemaRetriever(catalog, settings.retrieval_top_tables),
        warehouse=Warehouse(settings, {t.name for t in catalog.tables}),
        llm=LLMRouter(settings),
        summarize=False,
    )


async def run_case(graph, deps: Deps, case: Case) -> CaseResult:
    res = CaseResult(id=case.id, category=case.category, question=case.question, expected_intent=case.intent)
    config = {"configurable": {"thread_id": f"eval-{case.id}-{uuid.uuid4().hex[:8]}"}}
    graph_input: Any = new_turn(case.question)
    start = time.perf_counter()
    try:
        for _ in range(MAX_RESUMES + 1):
            async for mode, chunk in graph.astream(
                graph_input, config, context=RunContext(), stream_mode=["updates", "custom"]
            ):
                if mode == "custom":
                    res.notices.append(chunk)
            snapshot = await graph.aget_state(config)
            if not snapshot.interrupts:
                break
            graph_input = Command(resume=case.clarification_answer or DEFAULT_CLARIFICATION)
    except AllProvidersFailedError as e:
        res.error, res.infra_error = str(e).splitlines()[0], True
    except Exception as e:  # noqa: BLE001 - record and keep evaluating
        res.error = f"{type(e).__name__}: {e}"
    res.latency_s = round(time.perf_counter() - start, 2)

    state = (await graph.aget_state(config)).values
    res.intent = state.get("intent")
    res.route_ok = res.intent == case.intent
    res.clarifications = state.get("clarifications", [])
    res.tables = state.get("tables", [])
    res.sql = state.get("sql")
    res.attempts = state.get("attempts", 0)
    res.llm_calls = state.get("llm_calls", [])
    if state.get("last_error") and not res.error:
        res.error = state["last_error"]

    if case.gold_sql and not res.infra_error:
        result = state.get("result")
        res.executed = result is not None
        if result is None:
            res.match, res.strict, res.reason = False, False, "no result (SQL failed or wrong route)"
        else:
            gold = await asyncio.to_thread(deps.warehouse.run, case.gold_sql)
            cmp = compare(gold.columns, gold.rows, result["columns"], result["rows"], case.order_matters)
            res.match, res.strict, res.reason = cmp.match, cmp.strict, cmp.reason
    return res


def completed_ids(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    done = set()
    for line in results_path.read_text().splitlines():
        row = json.loads(line)
        if not row.get("infra_error"):
            done.add(row["id"])
    return done


def latest_results(results_path: Path) -> list[dict[str, Any]]:
    """Last recorded result per case id (re-runs of infra failures supersede earlier lines)."""
    by_id: dict[str, dict[str, Any]] = {}
    for line in results_path.read_text().splitlines():
        row = json.loads(line)
        by_id[row["id"]] = row
    return list(by_id.values())


async def run_eval(
    settings: Settings,
    cases: list[Case],
    run_dir: Path,
    pace_s: float = 0.0,
    progress=print,
) -> list[dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    done = completed_ids(results_path)
    todo = [c for c in cases if c.id not in done]
    progress(f"{len(cases)} cases, {len(done)} already done, {len(todo)} to run")

    deps = build_eval_deps(settings)
    graph = build_graph(deps, InMemorySaver())
    streak = 0  # consecutive infrastructure failures
    try:
        for i, case in enumerate(todo, 1):
            res = await run_case(graph, deps, case)
            with results_path.open("a") as f:
                f.write(json.dumps(asdict(res)) + "\n")
            mark = "INFRA" if res.infra_error else ("PASS" if res.correct else "FAIL")
            models = sorted({c["model"] for c in res.llm_calls})
            progress(
                f"[{i}/{len(todo)}] {case.id:4} {mark:5} {res.latency_s:6.1f}s  "
                f"{res.reason or res.error or ''}  {','.join(models)}"
            )
            streak = streak + 1 if res.infra_error else 0
            if streak >= MAX_CONSECUTIVE_INFRA:
                progress(
                    f"Stopping: {streak} cases in a row had no LLM provider available "
                    "(quota exhausted?). Re-run with the same --name later to resume."
                )
                break
            if pace_s and i < len(todo):
                await asyncio.sleep(pace_s)
    finally:
        deps.warehouse.close()
    return latest_results(results_path)


def check_gold(settings: Settings, cases: list[Case], progress=print) -> bool:
    """Run every gold query; fail loudly on errors or empty results (an empty gold answer
    would let any broken query that also returns nothing score as correct)."""
    catalog = Catalog.load(settings.catalog_path)
    wh = Warehouse(settings, {t.name for t in catalog.tables})
    ok = True
    try:
        for c in cases:
            if not c.gold_sql:
                continue
            try:
                r = wh.run(c.gold_sql)
            except Exception as e:  # noqa: BLE001
                progress(f"{c.id}: ERROR {e}")
                ok = False
                continue
            flag = ""
            if r.row_count == 0:
                flag, ok = "  <-- EMPTY", False
            if r.truncated:
                flag, ok = "  <-- TRUNCATED (gold must fit max_result_rows)", False
            progress(f"{c.id}: {r.row_count} rows, {len(r.columns)} cols{flag}")
    finally:
        wh.close()
    return ok


def rescore(
    settings: Settings, cases: list[Case], results_path: Path, progress=print
) -> list[dict[str, Any]]:
    """Re-score stored predictions against the CURRENT gold set, without calling any LLM.

    Use after fixing a gold query or the comparator. Predictions are re-executed from the SQL
    recorded in results.jsonl; routing results are kept as recorded.
    """
    by_case = {c.id: c for c in cases}
    catalog = Catalog.load(settings.catalog_path)
    wh = Warehouse(settings, {t.name for t in catalog.tables})
    updated = []
    try:
        for row in latest_results(results_path):
            case = by_case.get(row["id"])
            if case and case.gold_sql and not row["infra_error"] and row["executed"] and row["sql"]:
                pred = wh.run(row["sql"])
                gold = wh.run(case.gold_sql)
                cmp = compare(gold.columns, gold.rows, pred.columns, pred.rows, case.order_matters)
                if (cmp.match, cmp.reason) != (row["match"], row["reason"]):
                    progress(f"{row['id']}: {row['match']} -> {cmp.match} ({cmp.reason})")
                row.update(match=cmp.match, strict=cmp.strict, reason=cmp.reason)
            updated.append(row)
    finally:
        wh.close()
    results_path.write_text("".join(json.dumps(r) + "\n" for r in updated))
    return updated
