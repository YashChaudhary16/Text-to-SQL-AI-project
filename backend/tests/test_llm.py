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


class _HTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_failure_reason_is_short_and_user_safe():
    from sqlagent.llm import failure_reason

    assert failure_reason(_HTTPError(429)) == "rate limit reached"
    assert failure_reason(_HTTPError(403)) == "not permitted for this key"
    assert failure_reason(_HTTPError(503)) == "provider error"
    assert failure_reason(ValueError("bad json")) == "unusable response"


@pytest.mark.asyncio
async def test_notifies_on_each_fallback(monkeypatch):
    r = router(cerebras_api_key="c")

    class Model:
        def __init__(self, ok):
            self.ok = ok

        def with_structured_output(self, *a, **k):
            return self

        async def ainvoke(self, _):
            if not self.ok:
                raise _HTTPError(429)
            return {"parsed": Out(x=1), "raw": None, "parsing_error": None}

    monkeypatch.setattr(r, "_model", lambda spec, key: Model(spec.provider == "cerebras"))
    seen = []
    await r.structured("sql", Out, [], notify=seen.append)
    assert seen == [{"type": "fallback", "role": "sql", "model": "groq:big", "reason": "rate limit reached"}]


def test_sdk_retries_disabled_so_fallback_is_immediate():
    r = router()
    model = next(m for _, m in r._candidates("sql", byo_key=None, escalate=False))
    assert model.max_retries == 0


class _RateLimited(Exception):
    status_code = 429

    def __init__(self, seconds):
        super().__init__(f"Rate limit reached. Please try again in {seconds}s.")


def _scripted_models(r, monkeypatch, behaviour):
    """behaviour[label] is a list of outcomes per call: an Exception to raise or True to succeed."""
    calls = []

    class Model:
        def __init__(self, label):
            self.label = label

        def with_structured_output(self, *a, **k):
            return self

        async def ainvoke(self, _):
            calls.append(self.label)
            outcome = behaviour[self.label].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return {"parsed": Out(x=1), "raw": None, "parsing_error": None}

    monkeypatch.setattr(r, "_model", lambda spec, key: Model(spec.label))
    monkeypatch.setattr("sqlagent.llm.asyncio.sleep", _no_sleep)
    return calls


async def _no_sleep(_):
    return None


def test_retry_after_parsing():
    from sqlagent.llm import retry_after_seconds

    assert retry_after_seconds(_RateLimited(7.5)) == 7.5
    assert retry_after_seconds(_HTTPError(500)) is None


@pytest.mark.asyncio
async def test_short_rate_limit_waits_and_retries_before_local_model(monkeypatch):
    r = router(sql_models="groq:big,ollama:local", ollama_base_url="http://localhost:11434")
    calls = _scripted_models(r, monkeypatch, {"groq:big": [_RateLimited(5), True], "ollama:local": [True]})
    seen = []
    _, rec = await r.structured("sql", Out, [], notify=seen.append)
    assert calls == ["groq:big", "groq:big"]  # waited and retried groq; never touched the slow local model
    assert rec.model == "groq:big"
    assert {"type": "waiting", "role": "sql", "seconds": 5} in seen


@pytest.mark.asyncio
async def test_long_rate_limit_skips_wait_and_uses_local_model(monkeypatch):
    r = router(sql_models="groq:big,ollama:local", ollama_base_url="http://localhost:11434")
    calls = _scripted_models(r, monkeypatch, {"groq:big": [_RateLimited(90)], "ollama:local": [True]})
    seen = []
    _, rec = await r.structured("sql", Out, [], notify=seen.append)
    assert calls == ["groq:big", "ollama:local"]
    assert rec.model == "ollama:local" and not any(e["type"] == "waiting" for e in seen)


@pytest.mark.asyncio
async def test_still_limited_after_wait_raises_clear_error(monkeypatch):
    r = router()
    _scripted_models(r, monkeypatch, {"groq:big": [_RateLimited(3), _RateLimited(3)]})
    with pytest.raises(AllProvidersFailedError):
        await r.structured("sql", Out, [])
