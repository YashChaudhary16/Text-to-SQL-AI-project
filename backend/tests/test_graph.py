"""End-to-end graph tests: scripted LLM, real TPC-DS warehouse, in-memory checkpointer."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from sqlagent.graph.agent import build_graph
from sqlagent.graph.state import RunContext, new_turn

from .conftest import requires_warehouse

pytestmark = [requires_warehouse, pytest.mark.asyncio]

REVENUE_BY_YEAR = """
SELECT d.d_year AS year, ROUND(SUM(ss.ss_net_paid), 2) AS total_revenue
FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
GROUP BY d.d_year ORDER BY d.d_year
"""


def understanding(**overrides):
    base = dict(
        intent="data_question",
        standalone_question="What is store revenue by year?",
        tables=["store_sales", "date_dim"],
        assumptions=["revenue = net paid"],
        needs_clarification=False,
        clarifying_question="",
        options=[],
    )
    return {**base, **overrides}


def cfg(thread="t1"):
    return {"configurable": {"thread_id": thread}}


async def test_happy_path(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push("router", understanding())
    fake_llm.push("sql", {"plan": "sum by year", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "Revenue peaked in 2001.")

    out = await graph.ainvoke(new_turn("revenue by year?"), cfg(), context=RunContext())

    assert out["answer"] == "Revenue peaked in 2001."
    assert out["result"]["columns"] == ["year", "total_revenue"]
    assert 5 <= out["result"]["row_count"] <= 7
    assert out["attempts"] == 1
    assert [c["role"] for c in out["llm_calls"]] == ["router", "sql", "router"]
    assert out["history"][-1]["sql"] is not None
    # The SQL writer only saw the retrieved slice of the schema, not all 24 tables.
    sql_system = next(m for role, m in fake_llm.prompts if role == "sql")[0].content
    assert "TABLE store_sales" in sql_system and "TABLE web_returns" not in sql_system


async def test_clarification_interrupt_and_resume(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push(
        "router",
        understanding(
            needs_clarification=True,
            clarifying_question="Best by revenue or by number of orders?",
            options=["Revenue", "Number of orders"],
        ),
        understanding(standalone_question="Top customers by store revenue"),
    )
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "Done.")

    first = await graph.ainvoke(new_turn("who are our best customers?"), cfg("t2"), context=RunContext())
    interrupts = first["__interrupt__"]
    assert interrupts[0].value["question"] == "Best by revenue or by number of orders?"
    assert interrupts[0].value["options"] == ["Revenue", "Number of orders"]

    final = await graph.ainvoke(Command(resume="Revenue"), cfg("t2"), context=RunContext())
    assert final["answer"] == "Done."
    assert final["clarifications"] == [
        {"question": "Best by revenue or by number of orders?", "answer": "Revenue"}
    ]
    # The second understand call saw the user's clarification answer.
    second_router_prompt = [m for role, m in fake_llm.prompts if role == "router"][1][1].content
    assert "User answered: Revenue" in second_router_prompt


async def test_self_correction_after_sql_error(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push("router", understanding())
    fake_llm.push(
        "sql",
        {"plan": "", "sql": "SELECT ss_revenue FROM store_sales"},  # nonexistent column
        {"plan": "", "sql": REVENUE_BY_YEAR},
    )
    fake_llm.push("text", "Fixed.")

    out = await graph.ainvoke(new_turn("revenue by year?"), cfg("t3"), context=RunContext())
    assert out["attempts"] == 2
    assert out["answer"] == "Fixed."
    retry_prompt = [m for role, m in fake_llm.prompts if role == "sql"][1][1].content
    assert "ss_revenue" in retry_prompt and "previous attempt failed" in retry_prompt


async def test_unsafe_sql_is_never_executed_and_gives_up(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push("router", understanding())
    fake_llm.push("sql", *[{"plan": "", "sql": "DROP TABLE store_sales"}] * deps.settings.max_sql_attempts)

    out = await graph.ainvoke(new_turn("delete everything"), cfg("t4"), context=RunContext())
    assert out["result"] is None
    assert "couldn't produce a working query" in out["answer"]
    assert deps.warehouse.run("SELECT COUNT(*) AS n FROM store_sales").rows[0][0] > 0


async def test_out_of_scope_skips_sql(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push("router", understanding(intent="out_of_scope", tables=[]))
    out = await graph.ainvoke(new_turn("what's the weather?"), cfg("t5"), context=RunContext())
    assert "only answer questions" in out["answer"]
    assert out["sql"] is None


async def test_follow_up_sees_history(deps, fake_llm):
    graph = build_graph(deps, InMemorySaver())
    fake_llm.push(
        "router", understanding(), understanding(standalone_question="Store revenue by month in 2002")
    )
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR}, {"plan": "", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "Answer one.", "Answer two.")

    await graph.ainvoke(new_turn("revenue by year?"), cfg("t6"), context=RunContext())
    out = await graph.ainvoke(new_turn("now by month for 2002"), cfg("t6"), context=RunContext())
    assert len(out["history"]) == 2
    router_prompt = [m for role, m in fake_llm.prompts if role == "router"][-1][1].content
    assert "Conversation so far" in router_prompt and "Answer one." in router_prompt
