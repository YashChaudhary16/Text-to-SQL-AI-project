from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from sqlagent.config import Settings
from sqlagent.db.warehouse import Warehouse
from sqlagent.graph.agent import Deps
from sqlagent.llm import CallRecord
from sqlagent.retrieval import SchemaRetriever
from sqlagent.semantic.catalog import Catalog

BACKEND = Path(__file__).resolve().parents[1]
WAREHOUSE = BACKEND / "data" / "tpcds_sf1.duckdb"

requires_warehouse = pytest.mark.skipif(
    not WAREHOUSE.exists(), reason="run `uv run sqlagent generate-data --sf 1` first"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None, warehouse_url=str(WAREHOUSE), max_result_rows=50)


@pytest.fixture(scope="session")
def catalog(settings) -> Catalog:
    return Catalog.load(settings.catalog_path)


@pytest.fixture(scope="session")
def warehouse(settings, catalog):
    if not WAREHOUSE.exists():
        pytest.skip("warehouse not generated")
    wh = Warehouse(settings, {t.name for t in catalog.tables})
    yield wh
    wh.close()


class ScriptedLLM:
    """Stands in for LLMRouter: returns queued responses per role, records prompts."""

    def __init__(self):
        self.queues: dict[str, deque] = {"router": deque(), "sql": deque(), "text": deque()}
        self.prompts: list[tuple[str, list]] = []

    def push(self, kind: str, *items) -> None:
        self.queues[kind].extend(items)

    async def structured(self, role, schema, messages, **kwargs):
        self.prompts.append((role, messages))
        item = self.queues[role].popleft()
        return (item if isinstance(item, schema) else schema(**item)), CallRecord(
            role=role, model="fake:model"
        )

    async def text(self, role, messages, **kwargs):
        self.prompts.append(("text", messages))
        return self.queues["text"].popleft(), CallRecord(role=role, model="fake:model")


@pytest.fixture
def fake_llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def deps(settings, catalog, warehouse, fake_llm) -> Deps:
    return Deps(
        settings=settings,
        catalog=catalog,
        retriever=SchemaRetriever(catalog),
        warehouse=warehouse,
        llm=fake_llm,
    )
