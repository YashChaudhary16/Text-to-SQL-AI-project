"""The multi-agent text-to-SQL workflow.

understand ──(ambiguous)──> clarify ──(user answers, via interrupt)──> understand
    │
    ├─(out of scope)──> out_of_scope ──> END
    ├─(schema question)──> retrieve ──> explain_schema ──> END
    └─(data question)──> retrieve ──> write_sql ──> execute ──(ok)──> respond ──> END
                                          ^            │
                                          └──(error, attempts left)
                                                       └──(attempts exhausted)──> give_up ──> END
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from dataclasses import dataclass

import duckdb
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from sqlagent.config import Settings
from sqlagent.db.guard import UnsafeSQLError
from sqlagent.db.warehouse import QueryTimeoutError, Warehouse
from sqlagent.graph import prompts
from sqlagent.graph.state import AgentState, RunContext, SQLDraft, Turn, Understanding
from sqlagent.llm import CallRecord, LLMRouter
from sqlagent.retrieval import SchemaRetriever
from sqlagent.semantic.catalog import Catalog

HISTORY_TURNS = 3
SQL_START = re.compile(r"^\s*(select|with|from)\b", re.IGNORECASE)
PREVIEW_ROWS = 30
SCHEMA_DETAIL_TABLES = 4


@dataclass
class Deps:
    settings: Settings
    catalog: Catalog
    retriever: SchemaRetriever
    warehouse: Warehouse
    llm: LLMRouter


class SQLAgent:
    def __init__(self, deps: Deps):
        self.d = deps
        self._overview = deps.catalog.render_overview()

    # --- nodes ---------------------------------------------------------------------------
    async def understand(self, state: AgentState, runtime: Runtime[RunContext]) -> AgentState:
        s = self.d.settings
        asked = len(state.get("clarifications", []))
        budget = (
            "You have already asked the user enough questions: do NOT ask again; apply defaults."
            if asked >= s.max_clarifications
            else "Ask at most one clarifying question at a time."
        )
        system = prompts.UNDERSTAND_SYSTEM.format(overview=self._overview, clarification_budget=budget)
        parts = []
        if history := state.get("history"):
            parts.append("Conversation so far:")
            for t in history[-HISTORY_TURNS:]:
                parts.append(f"- Q: {t['question']}\n  A: {t['answer'][:400]}")
        parts.append(f"User request: {state['question']}")
        for c in state.get("clarifications", []):
            parts.append(f"You asked: {c['question']}\nUser answered: {c['answer']}")

        out, rec = await self.d.llm.structured(
            "router",
            Understanding,
            [SystemMessage(system), HumanMessage("\n\n".join(parts))],
            byo_key=runtime.context.anthropic_api_key if runtime.context else None,
        )
        known = self.d.catalog.by_name
        standalone = out.standalone_question.strip()
        if not standalone or SQL_START.match(standalone):
            # Small models sometimes answer with SQL here; SQL is the next agent's job.
            standalone = state["question"]
        return {
            "intent": out.intent,
            "standalone_question": standalone,
            "linked_tables": [t for t in out.tables if t in known],
            "assumptions": out.assumptions,
            "llm_calls": self._log(state, rec),
            **(
                {"pending_clarification": {"question": out.clarifying_question, "options": out.options}}
                if out.needs_clarification and out.clarifying_question and asked < s.max_clarifications
                else {"pending_clarification": None}
            ),
        }

    async def clarify(self, state: AgentState) -> AgentState:
        pending = state["pending_clarification"]
        answer = interrupt({"type": "clarification", **pending})
        return {
            "clarifications": [
                *state.get("clarifications", []),
                {"question": pending["question"], "answer": str(answer)},
            ],
            "pending_clarification": None,
        }

    async def retrieve(self, state: AgentState) -> AgentState:
        res = self.d.retriever.retrieve(state["standalone_question"], state.get("linked_tables"))
        return {"tables": res.tables}

    async def write_sql(self, state: AgentState, runtime: Runtime[RunContext]) -> AgentState:
        s = self.d.settings
        schema = self.d.catalog.render_tables(state["tables"])
        system = prompts.SQL_SYSTEM.format(schema=schema, max_rows=s.max_result_rows)
        context = ""
        if state.get("assumptions"):
            context += "Assumptions to apply: " + "; ".join(state["assumptions"]) + "\n"
        if state.get("last_error"):
            context += prompts.SQL_RETRY.format(sql=state.get("sql"), error=state["last_error"])
        user = prompts.SQL_USER.format(question=state["standalone_question"], context=context)

        out, rec = await self.d.llm.structured(
            "sql",
            SQLDraft,
            [SystemMessage(system), HumanMessage(user)],
            byo_key=runtime.context.anthropic_api_key if runtime.context else None,
            escalate=state.get("attempts", 0) >= 2,
        )
        return {"sql": out.sql.strip(), "llm_calls": self._log(state, rec)}

    async def execute(self, state: AgentState) -> AgentState:
        attempts = state.get("attempts", 0) + 1
        try:
            result = await asyncio.to_thread(self.d.warehouse.run, state["sql"])
        except (UnsafeSQLError, QueryTimeoutError, duckdb.Error) as e:
            return {"attempts": attempts, "last_error": f"{type(e).__name__}: {e}", "result": None}
        return {
            "attempts": attempts,
            "last_error": None,
            "sql": result.sql,
            "result": {
                "columns": result.columns,
                "rows": result.rows,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "elapsed_ms": result.elapsed_ms,
            },
        }

    async def respond(self, state: AgentState, runtime: Runtime[RunContext]) -> AgentState:
        r = state["result"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(r["columns"])
        w.writerows(r["rows"][:PREVIEW_ROWS])
        user = prompts.RESPOND_USER.format(
            question=state["standalone_question"],
            assumptions="; ".join(state.get("assumptions", [])) or "none",
            sql=state["sql"],
            row_count=r["row_count"],
            truncated=", truncated" if r["truncated"] else "",
            preview=buf.getvalue(),
        )
        text, rec = await self.d.llm.text(
            "router",
            [SystemMessage(prompts.RESPOND_SYSTEM), HumanMessage(user)],
            byo_key=runtime.context.anthropic_api_key if runtime.context else None,
        )
        return self._finish(state, text, rec)

    async def explain_schema(self, state: AgentState, runtime: Runtime[RunContext]) -> AgentState:
        # Always give the whole-warehouse overview: broad questions ("what's this data about?")
        # have nothing specific to retrieve, and a retrieved slice alone skews the answer.
        # Add column detail only for the few tables the intake agent linked.
        detail_tables = state.get("linked_tables", [])[:SCHEMA_DETAIL_TABLES]
        detail = self.d.catalog.render_tables(detail_tables) if detail_tables else "(none)"
        text, rec = await self.d.llm.text(
            "router",
            [
                SystemMessage(prompts.SCHEMA_SYSTEM.format(overview=self._overview, detail=detail)),
                HumanMessage(state["standalone_question"]),
            ],
            byo_key=runtime.context.anthropic_api_key if runtime.context else None,
        )
        return self._finish(state, text, rec)

    async def out_of_scope(self, state: AgentState) -> AgentState:
        return self._finish(state, prompts.OUT_OF_SCOPE, None)

    async def give_up(self, state: AgentState) -> AgentState:
        msg = (
            f"I couldn't produce a working query after {state['attempts']} attempts. "
            f"Last error: {state['last_error']}\n\nTry rephrasing, narrowing the time range, "
            "or naming the metric you want (e.g. revenue, units sold, profit)."
        )
        return self._finish(state, msg, None)

    # --- routing -------------------------------------------------------------------------
    def route_after_understand(self, state: AgentState) -> str:
        if state.get("pending_clarification"):
            return "clarify"
        if state["intent"] == "out_of_scope":
            return "out_of_scope"
        return "retrieve"

    def route_after_retrieve(self, state: AgentState) -> str:
        return "explain_schema" if state["intent"] == "schema_question" else "write_sql"

    def route_after_execute(self, state: AgentState) -> str:
        if state.get("result") is not None:
            return "respond"
        if state["attempts"] < self.d.settings.max_sql_attempts:
            return "write_sql"
        return "give_up"

    # --- helpers -------------------------------------------------------------------------
    @staticmethod
    def _log(state: AgentState, rec: CallRecord) -> list[dict]:
        return [*state.get("llm_calls", []), rec.as_dict()]

    def _finish(self, state: AgentState, answer: str, rec: CallRecord | None) -> AgentState:
        turn = Turn(
            question=state.get("standalone_question") or state["question"],
            sql=state.get("sql") if state.get("result") else None,
            answer=answer,
        )
        update: AgentState = {"answer": answer, "history": [*state.get("history", []), turn]}
        if rec:
            update["llm_calls"] = self._log(state, rec)
        return update


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
    agent = SQLAgent(deps)
    g = StateGraph(AgentState, context_schema=RunContext)
    g.add_node("understand", agent.understand)
    g.add_node("clarify", agent.clarify)
    g.add_node("retrieve", agent.retrieve)
    g.add_node("write_sql", agent.write_sql)
    g.add_node("execute", agent.execute)
    g.add_node("respond", agent.respond)
    g.add_node("explain_schema", agent.explain_schema)
    g.add_node("out_of_scope", agent.out_of_scope)
    g.add_node("give_up", agent.give_up)

    g.add_edge(START, "understand")
    g.add_conditional_edges(
        "understand", agent.route_after_understand, ["clarify", "out_of_scope", "retrieve"]
    )
    g.add_edge("clarify", "understand")
    g.add_conditional_edges("retrieve", agent.route_after_retrieve, ["explain_schema", "write_sql"])
    g.add_edge("write_sql", "execute")
    g.add_conditional_edges("execute", agent.route_after_execute, ["respond", "write_sql", "give_up"])
    for terminal in ("respond", "explain_schema", "out_of_scope", "give_up"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)
