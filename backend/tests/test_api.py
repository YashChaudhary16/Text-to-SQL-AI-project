import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from sqlagent.api import app as api
from sqlagent.graph.agent import build_graph

from .conftest import requires_warehouse
from .test_graph import REVENUE_BY_YEAR, understanding

pytestmark = requires_warehouse


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events, name = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            events.append((name, json.loads(line.split(":", 1)[1].strip())))
            name = None
    return events


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch, settings):
    """The app reads settings via get_settings(), which loads the developer's real .env
    (Neon, MotherDuck, Groq). Tests must never reach those, so pin isolated settings."""
    test_settings = settings.model_copy(
        update={"database_url": "", "groq_api_key": "", "rate_limit_per_minute": 10}
    )
    monkeypatch.setattr(api, "get_settings", lambda: test_settings)
    return test_settings


@pytest.fixture
def client(deps):
    api._hits.clear()
    with TestClient(api.app) as c:
        c.app.state.graph = build_graph(deps, InMemorySaver())
        yield c


def test_chat_streams_steps_result_and_answer(client, fake_llm):
    fake_llm.push("router", understanding())
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "Revenue was flat.")

    r = client.post("/api/chat", json={"message": "revenue by year"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "thread" and names[-1] == "done"
    assert names.index("result") < names.index("answer")
    result = dict(events)["result"]
    assert result["columns"] == ["year", "total_revenue"] and "SELECT" in result["sql"]
    assert dict(events)["answer"]["text"] == "Revenue was flat."


def test_clarification_round_trip_over_http(client, fake_llm):
    fake_llm.push(
        "router",
        understanding(
            needs_clarification=True,
            clarifying_question="By revenue or orders?",
            options=["Revenue", "Orders"],
        ),
        understanding(),
    )
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "Here you go.")

    first = parse_sse(client.post("/api/chat", json={"message": "best customers?"}).text)
    thread_id = dict(first)["thread"]["thread_id"]
    assert dict(first)["clarification"] == {
        "question": "By revenue or orders?",
        "options": ["Revenue", "Orders"],
    }
    assert "answer" not in dict(first)

    second = parse_sse(client.post("/api/chat", json={"thread_id": thread_id, "message": "Revenue"}).text)
    assert dict(second)["answer"]["text"] == "Here you go."


def test_rate_limit_applies_without_byo_key(client, fake_llm, monkeypatch):
    monkeypatch.setattr(api.get_settings(), "rate_limit_per_minute", 1)
    fake_llm.push(
        "router",
        understanding(intent="out_of_scope", tables=[]),
        understanding(intent="out_of_scope", tables=[]),
    )
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 429
    # BYO-key requests are exempt.
    r = client.post("/api/chat", json={"message": "hi"}, headers={"X-Anthropic-Key": "sk-ant-test"})
    assert r.status_code == 200


def test_rejects_malformed_byo_key(client):
    r = client.post("/api/chat", json={"message": "hi"}, headers={"X-Anthropic-Key": "not-a-key"})
    assert r.status_code == 400


def test_schema_endpoint(client):
    body = client.get("/api/schema").json()
    assert len(body["tables"]) == 24
    assert body["coverage"]["store_sales"]["max_date"].startswith("2003")


def test_fallback_notice_is_streamed(client, fake_llm, monkeypatch):
    original = fake_llm.structured

    async def structured_with_fallback(role, schema, messages, **kwargs):
        if role == "sql" and kwargs.get("notify"):
            kwargs["notify"](
                {"type": "fallback", "role": "sql", "model": "groq:x", "reason": "rate limit reached"}
            )
        return await original(role, schema, messages, **kwargs)

    monkeypatch.setattr(fake_llm, "structured", structured_with_fallback)
    fake_llm.push("router", understanding())
    fake_llm.push("sql", {"plan": "", "sql": REVENUE_BY_YEAR})
    fake_llm.push("text", "ok")
    events = parse_sse(client.post("/api/chat", json={"message": "revenue by year"}).text)
    names = [n for n, _ in events]
    assert "notice" in names and names.index("notice") < names.index("result")
    assert dict(events)["notice"]["reason"] == "rate limit reached"


def test_startup_failure_is_reported_not_fatal(monkeypatch):
    from sqlagent.db import warehouse as wh_mod

    def boom(*a, **k):
        raise RuntimeError("cannot open postgresql://neondb_owner:s3cretpw@ep-x-pooler.neon.tech/db")

    monkeypatch.setattr(api, "Warehouse", boom)
    with TestClient(api.app) as c:
        h = c.get("/api/health").json()
        assert h["status"] == "error" and h["startup"]["stage"] == "warehouse"
        assert "s3cretpw" not in h["startup"]["error"] and "<redacted>" in h["startup"]["error"]
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 503
        assert c.get("/api/schema").status_code == 503
    assert wh_mod  # module import kept for clarity


def test_redact_hides_credentials():
    text = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc gsk_abcdefghijklmnop sk-ant-api03-xyzxyzxyz"
    out = api.redact(text)
    assert "eyJ" not in out and "gsk_abc" not in out and "sk-ant-api03" not in out


def test_default_duckdb_home_falls_back_without_home(monkeypatch):
    from sqlagent.config import _default_duckdb_home

    monkeypatch.delenv("HOME", raising=False)
    assert _default_duckdb_home() == "/tmp/duckdb"
    monkeypatch.setenv("HOME", "/")  # not writable for a normal user
    assert _default_duckdb_home() in ("", "/tmp/duckdb")
