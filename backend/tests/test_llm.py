import pytest
from pydantic import BaseModel

from sqlagent.config import Settings
from sqlagent.llm import AllProvidersFailedError, LLMRouter


def router(**kw) -> LLMRouter:
    base = dict(
        _env_file=None,
        router_models="groq:small,ollama:local",
        sql_models="groq:big,cerebras:big,ollama:local",
        groq_api_key="g",
        cerebras_api_key="",
        ollama_base_url="",
        anthropic_api_key="",
    )
    return LLMRouter(Settings(**{**base, **kw}))


def labels(r: LLMRouter, role="sql", **kw) -> list[str]:
    return [
        label for label, _ in r._candidates(role, byo_key=kw.get("byo"), escalate=kw.get("escalate", False))
    ]


def test_skips_providers_without_credentials():
    assert labels(router()) == ["groq:big"]
    assert labels(router(ollama_base_url="http://localhost:11434")) == ["groq:big", "ollama:local"]


def test_byo_key_puts_claude_first_for_that_request():
    assert labels(router(), byo="sk-ant-x") == ["anthropic:claude-opus-5-5", "groq:big"]
    assert labels(router(), role="router", byo="sk-ant-x")[0] == "anthropic:claude-haiku-5-5"


def test_owner_key_unused_unless_enabled():
    assert labels(router(anthropic_api_key="sk-ant-owner")) == ["groq:big"]
    enabled = router(anthropic_api_key="sk-ant-owner", owner_anthropic_enabled=True)
    assert labels(enabled) == ["groq:big", "anthropic:claude-opus-5-5"]  # last resort
    assert labels(enabled, escalate=True)[0] == "anthropic:claude-opus-5-5"  # escalation


def test_escalation_without_any_claude_key_keeps_open_models():
    assert labels(router(), escalate=True) == ["groq:big"]


class Out(BaseModel):
    x: int


@pytest.mark.asyncio
async def test_falls_through_and_reports_all_errors(monkeypatch):
    r = router(cerebras_api_key="c")

    class Boom:
        def __init__(self, name):
            self.name = name

        def with_structured_output(self, *a, **k):
            return self

        async def ainvoke(self, _):
            raise RuntimeError(f"{self.name} down")

    monkeypatch.setattr(r, "_model", lambda spec, key: Boom(spec.label))
    with pytest.raises(AllProvidersFailedError) as e:
        await r.structured("sql", Out, [])
    assert "groq:big" in str(e.value) and "cerebras:big" in str(e.value)


@pytest.mark.asyncio
async def test_records_fallbacks_and_usage(monkeypatch):
    r = router(cerebras_api_key="c")

    class Raw:
        usage_metadata = {"input_tokens": 120, "output_tokens": 30}

    class Model:
        def __init__(self, ok):
            self.ok = ok

        def with_structured_output(self, *a, **k):
            assert k["method"] == "json_schema"  # never forced tool_choice
            return self

        async def ainvoke(self, _):
            if not self.ok:
                raise RuntimeError("429")
            return {"parsed": Out(x=1), "raw": Raw(), "parsing_error": None}

    monkeypatch.setattr(r, "_model", lambda spec, key: Model(spec.provider == "cerebras"))
    out, rec = await r.structured("sql", Out, [])
    assert out.x == 1
    assert rec.model == "cerebras:big" and rec.fallbacks == ["groq:big"]
    assert (rec.input_tokens, rec.output_tokens) == (120, 30)
