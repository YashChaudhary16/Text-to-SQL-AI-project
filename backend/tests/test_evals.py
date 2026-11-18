from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sqlagent.evals.compare import compare
from sqlagent.evals.report import call_cost, summarize
from sqlagent.evals.runner import Case, run_case
from sqlagent.graph.agent import build_graph

from .conftest import requires_warehouse
from .test_graph import REVENUE_BY_YEAR, understanding

G_COLS, G_ROWS = ["cat", "rev"], [["Music", Decimal("194490482.39")], ["Home", Decimal("194044418.67")]]


def test_exact_match():
    assert compare(G_COLS, G_ROWS, ["category", "revenue"], G_ROWS).match


def test_extra_predicted_columns_allowed_but_not_strict():
    pred = [["Music", "AAA", 194490482.39], ["Home", "BBB", 194044418.67]]
    c = compare(G_COLS, G_ROWS, ["c", "id", "r"], pred)
    assert c.match and not c.strict


def test_rounding_within_tolerance_and_reordered_columns():
    pred = [[194490482.390001, "Music"], [194044418.67, "Home"]]
    assert compare(G_COLS, G_ROWS, ["r", "c"], pred).match


def test_order_only_checked_when_it_matters():
    swapped = list(reversed(G_ROWS))
    assert compare(G_COLS, G_ROWS, G_COLS, swapped).match
    assert not compare(G_COLS, G_ROWS, G_COLS, swapped, order_matters=True).match


def test_wrong_values_and_row_counts_fail():
    assert not compare(G_COLS, G_ROWS, G_COLS, [["Music", 1.0], ["Home", 2.0]]).match
    assert compare(G_COLS, G_ROWS, G_COLS, G_ROWS[:1]).reason.startswith("row count")


def test_columns_matching_separately_must_also_line_up_as_rows():
    # Same column value sets, but rows are paired differently.
    pred = [["Music", Decimal("194044418.67")], ["Home", Decimal("194490482.39")]]
    assert not compare(G_COLS, G_ROWS, G_COLS, pred).match


def test_percentage_vs_fraction_is_a_miss_by_design():
    assert not compare(["share"], [[48.45]], ["share"], [[0.4845]]).match


def test_cost_uses_price_table_and_free_tier_is_zero():
    assert call_cost({"model": "groq:openai/gpt-oss-120b", "input_tokens": 10_000, "output_tokens": 500}) == 0
    opus = {"model": "anthropic:claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000}
    assert call_cost(opus) == pytest.approx(4.0 + 2.0)


def test_summary_counts_routing_only_cases_separately():
    base = dict(infra_error=False, clarifications=[], attempts=1, llm_calls=[], notices=[], latency_s=1.0)
    results = [
        {**base, "id": "e1", "category": "easy", "route_ok": True, "match": True, "strict": True},
        {**base, "id": "e2", "category": "easy", "route_ok": True, "match": False, "strict": False},
        {**base, "id": "s1", "category": "schema", "route_ok": True, "match": None, "strict": None},
        {
            **base,
            "id": "x",
            "category": "easy",
            "route_ok": False,
            "match": None,
            "strict": None,
            "infra_error": True,
        },
    ]
    s = summarize(results, {"name": "t"})
    assert s["execution_accuracy"] == 0.5 and s["data_cases"] == 2
    assert s["routing_accuracy"] == 1.0 and s["infra_failures"] == 1


@requires_warehouse
@pytest.mark.asyncio
async def test_run_case_scores_against_gold_and_answers_clarifications(deps, fake_llm):
    deps.summarize = False
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push(
        "router",
        understanding(needs_clarification=True, clarifying_question="Which metric?", options=["Revenue"]),
        understanding(),
    )
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR})
    case = Case(
        id="t1",
        category="ambiguous",
        question="revenue by year?",
        gold_sql="SELECT d_year, SUM(ss_net_paid) FROM store_sales "
        "JOIN date_dim ON ss_sold_date_sk = d_date_sk GROUP BY d_year",
        clarification_answer="Revenue",
    )
    res = await run_case(graph, deps, case)
    assert res.match and res.route_ok and res.correct
    assert res.clarifications == [{"question": "Which metric?", "answer": "Revenue"}]
    assert res.llm_calls and not res.infra_error
