"""Graph state, runtime context and structured LLM outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


@dataclass
class RunContext:
    """Per-request values that must NOT be persisted.

    LangGraph copies string values from `config["configurable"]` into checkpoint metadata,
    so secrets such as a visitor's API key travel through the runtime context instead.
    """

    anthropic_api_key: str | None = None


class Turn(TypedDict):
    question: str
    sql: str | None
    answer: str


class AgentState(TypedDict, total=False):
    # Persisted across turns of a conversation thread.
    history: list[Turn]

    # Per-turn fields (reset by the API at the start of every new question).
    question: str
    clarifications: list[dict[str, str]]  # [{"question": ..., "answer": ...}]
    intent: str
    pending_clarification: dict[str, Any] | None  # {"question": ..., "options": [...]}
    standalone_question: str
    linked_tables: list[str]
    assumptions: list[str]
    tables: list[str]
    sql: str | None
    attempts: int
    last_error: str | None
    result: dict[str, Any] | None
    answer: str | None
    llm_calls: list[dict[str, Any]]


def new_turn(question: str) -> AgentState:
    return AgentState(
        question=question,
        clarifications=[],
        intent="",
        pending_clarification=None,
        standalone_question="",
        linked_tables=[],
        assumptions=[],
        tables=[],
        sql=None,
        attempts=0,
        last_error=None,
        result=None,
        answer=None,
        llm_calls=[],
    )


# --- structured LLM outputs ------------------------------------------------------------------
class Understanding(BaseModel):
    """Output of the intent/clarification agent."""

    intent: Literal["data_question", "schema_question", "out_of_scope"] = Field(
        description="data_question: asks for numbers, lists, rankings, totals, trends or comparisons "
        "(anything answered by querying rows); schema_question: asks only what data exists or "
        "what a field means; out_of_scope: unrelated to this retail warehouse."
    )
    standalone_question: str = Field(
        description="The user's request rewritten in plain English as one self-contained question, "
        "folding in conversation history and any clarification answers. Natural language only: "
        "never SQL."
    )
    tables: list[str] = Field(
        description="Names of the tables needed to answer, from the table list. Empty if out of scope."
    )
    assumptions: list[str] = Field(
        description="Reasonable defaults you applied instead of asking, e.g. 'all sales channels "
        "combined', \"'last year' = 2002, the latest full year in the data\"."
    )
    needs_clarification: bool = Field(
        description="True only if the question is ambiguous in a way that would materially "
        "change the answer and no sensible default exists."
    )
    clarifying_question: str = Field(description="The question to ask the user, or empty string.")
    options: list[str] = Field(description="2-4 short suggested answers, or empty list.")


class SQLDraft(BaseModel):
    """Output of the SQL-writer agent."""

    plan: str = Field(description="1-3 sentences: tables, joins, filters and aggregation used.")
    sql: str = Field(description="A single DuckDB SELECT statement.")
