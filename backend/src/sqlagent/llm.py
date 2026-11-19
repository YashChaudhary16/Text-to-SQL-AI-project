"""Provider-agnostic LLM access with ordered fallbacks.

Each graph node asks for a *role* ("router" or "sql"), never a provider. The role maps to
an ordered chain of "provider:model" specs; providers without credentials are skipped,
and on any error (rate limit, quota, timeout, unparseable output) the next one is tried.

Claude sits in the chain in one of three ways:
  * the visitor supplied their own key (BYO)  -> Claude first, for that request only
  * escalation after repeated SQL failures    -> Claude first, if a key is available
  * owner key explicitly enabled              -> Claude last, as the final fallback
A chain that lists an `anthropic:` model itself (used to pin evaluation runs) is taken as written
instead: those entries use the BYO or enabled owner key, and none of the three above is added.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from sqlagent.config import Settings

log = logging.getLogger(__name__)

Role = Literal["router", "sql"]
T = TypeVar("T", bound=BaseModel)

LOCAL_PROVIDERS = ("ollama:",)  # slow on consumer hardware: tried last

OPENAI_COMPATIBLE = {
    "cerebras": ("https://api.cerebras.ai/v1", "cerebras_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, raw: str) -> ModelSpec:
        provider, _, model = raw.strip().partition(":")
        if not model:
            raise ValueError(f"Model spec must be 'provider:model', got {raw!r}")
        return cls(provider.lower(), model)


@dataclass
class CallRecord:
    role: Role
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    fallbacks: list[str] = field(default_factory=list)  # models that failed before this one

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class AllProvidersFailedError(RuntimeError):
    pass


Notify = Callable[[dict[str, Any]], None]


_RETRY_IN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", re.IGNORECASE)


def _status(e: Exception) -> int | None:
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


def retry_after_seconds(e: Exception) -> float | None:
    """Seconds until a rate-limited (429) request may be retried, or None if not a 429."""
    if _status(e) != 429:
        return None
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        pass
    if m := _RETRY_IN.search(str(e)):
        minutes, value, unit = m.groups()
        secs = float(value) / (1000 if unit.lower() == "ms" else 1)
        return secs + 60 * int(minutes or 0)
    return 30.0  # rate-limited without a hint: assume part of a minute


def failure_reason(e: Exception) -> str:
    """Short, user-safe reason for a provider failure (no keys, no raw payloads)."""
    status = _status(e)
    if status == 429:
        return "rate limit reached"
    if status in (401, 403):
        return "not permitted for this key"
    if status and status >= 500:
        return "provider error"
    name = type(e).__name__.lower()
    if "timeout" in name:
        return "timed out"
    if "connect" in name:
        return "unreachable"
    if isinstance(e, ValueError):
        return "unusable response"
    return "failed"


class LLMRouter:
    def __init__(self, settings: Settings):
        self.s = settings
        self._chains: dict[Role, list[ModelSpec]] = {
            "router": [ModelSpec.parse(x) for x in settings.router_models.split(",") if x.strip()],
            "sql": [ModelSpec.parse(x) for x in settings.sql_models.split(",") if x.strip()],
        }
        self._cache: dict[tuple[ModelSpec, str | None], BaseChatModel] = {}

    # --- public API ----------------------------------------------------------------------
    async def structured(
        self,
        role: Role,
        schema: type[T],
        messages: list[BaseMessage],
        *,
        byo_key: str | None = None,
        escalate: bool = False,
        notify: Notify | None = None,
    ) -> tuple[T, CallRecord]:
        async def call(model: BaseChatModel) -> tuple[T, Any]:
            runnable = model.with_structured_output(schema, method="json_schema", include_raw=True)
            out = await runnable.ainvoke(messages)
            if out.get("parsed") is None:
                raise ValueError(f"Unparseable structured output: {out.get('parsing_error')}")
            return out["parsed"], out["raw"]

        return await self._run(role, call, byo_key=byo_key, escalate=escalate, notify=notify)

    async def text(
        self,
        role: Role,
        messages: list[BaseMessage],
        *,
        byo_key: str | None = None,
        notify: Notify | None = None,
    ) -> tuple[str, CallRecord]:
        async def call(model: BaseChatModel) -> tuple[str, Any]:
            msg = await model.ainvoke(messages)
            text = msg.text if isinstance(msg.text, str) else msg.text()
            if not text.strip():
                raise ValueError("Empty response")
            return text.strip(), msg

        return await self._run(role, call, byo_key=byo_key, notify=notify)

    def available_models(self, role: Role) -> list[str]:
        return [label for label, _ in self._candidates(role, byo_key=None, escalate=False)]

    # --- internals -----------------------------------------------------------------------
    async def _run(
        self,
        role: Role,
        call,
        *,
        byo_key: str | None,
        escalate: bool = False,
        notify: Notify | None = None,
    ):
        failed: list[str] = []
        errors: list[str] = []
        all_candidates = list(self._candidates(role, byo_key=byo_key, escalate=escalate))
        # Three phases: (1) hosted models in chain order; (2) if they were rate-limited and the
        # provider's wait is short, wait once and retry just those (free tiers limit tokens per
        # minute); (3) only then slow local models, the true last resort. A 10 s wait for a
        # hosted model beats a 2-minute answer from a local one.
        hosted = [c for c in all_candidates if not c[0].startswith(LOCAL_PROVIDERS)]
        local = [c for c in all_candidates if c[0].startswith(LOCAL_PROVIDERS)]
        phases: list[list[tuple[str, BaseChatModel]]] = [hosted, [], local]
        rate_limited: list[tuple[str, BaseChatModel]] = []
        waits: list[float] = []
        for phase, candidates in enumerate(phases):
            if phase == 1:
                wait = min(waits) if waits else None
                if wait is None or wait > self.s.max_rate_limit_wait_s:
                    continue
                wait = max(wait, 1.0)
                if notify:
                    notify({"type": "waiting", "role": role, "seconds": round(wait)})
                await asyncio.sleep(wait)
                candidates = rate_limited
            rate_limited, waits = [], []
            for label, model in candidates:
                try:
                    result, raw = await call(model)
                except Exception as e:  # noqa: BLE001 - any provider failure means "try the next"
                    log.warning("LLM %s failed (%s): %s", label, role, e)
                    failed.append(label)
                    errors.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")
                    if notify:
                        notify(
                            {"type": "fallback", "role": role, "model": label, "reason": failure_reason(e)}
                        )
                    if (wait_s := retry_after_seconds(e)) is not None:
                        rate_limited.append((label, model))
                        waits.append(wait_s)
                    continue
                usage = getattr(raw, "usage_metadata", None) or {}
                record = CallRecord(
                    role=role,
                    model=label,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    fallbacks=failed,
                )
                return result, record
        if not errors:
            raise AllProvidersFailedError(
                "No LLM provider is configured. Set GROQ_API_KEY (free), OLLAMA_BASE_URL, "
                "or supply your own Anthropic key."
            )
        raise AllProvidersFailedError("All LLM providers failed:\n" + "\n".join(errors))

    def _candidates(self, role: Role, *, byo_key: str | None, escalate: bool):
        claude_model = self.s.claude_router_model if role == "router" else self.s.claude_sql_model
        claude = ModelSpec("anthropic", claude_model)
        owner_key = self.s.anthropic_api_key if self.s.owner_anthropic_enabled else None
        claude_key = byo_key or owner_key

        chain = self._chains[role]
        # A chain that names Claude explicitly (e.g. an eval pinning `anthropic:claude-haiku-5-5`) is
        # used as written: those entries get the available key, and no implicit Claude model is
        # added, so a different Claude model can never serve the request.
        if any(spec.provider == "anthropic" for spec in chain):
            order = [(spec, claude_key if spec.provider == "anthropic" else None) for spec in chain]
        else:
            order = []
            if claude_key and (byo_key or escalate):
                order.append((claude, claude_key))
            order += [(spec, None) for spec in chain]
            if claude_key and not (byo_key or escalate):
                order.append((claude, claude_key))

        for spec, key in order:
            model = self._model(spec, key)
            if model is not None:
                yield spec.label, model

    def _model(self, spec: ModelSpec, api_key: str | None) -> BaseChatModel | None:
        # Never cache BYO-key clients beyond the process's own key, so visitor keys don't linger.
        cacheable = spec.provider != "anthropic" or api_key == self.s.anthropic_api_key
        cache_key = (spec, None if spec.provider != "anthropic" else "owner")
        if cacheable and cache_key in self._cache:
            return self._cache[cache_key]
        model = self._build(spec, api_key)
        if model is not None and cacheable:
            self._cache[cache_key] = model
        return model

    def _build(self, spec: ModelSpec, api_key: str | None) -> BaseChatModel | None:
        s = self.s
        p = spec.provider
        if p == "groq":
            if not s.groq_api_key:
                return None
            from langchain_groq import ChatGroq

            # max_retries=0: SDK retries honour retry-after (we saw a silent 39 s wait on 429).
            # Falling through to the next model in the chain is faster.
            return ChatGroq(model=spec.model, api_key=s.groq_api_key, timeout=s.llm_timeout_s, max_retries=0)
        if p in OPENAI_COMPATIBLE:
            base_url, key_attr = OPENAI_COMPATIBLE[p]
            key = getattr(s, key_attr)
            if not key:
                return None
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=spec.model, base_url=base_url, api_key=key, timeout=s.llm_timeout_s, max_retries=0
            )
        if p == "ollama":
            if not s.ollama_base_url:
                return None
            from langchain_ollama import ChatOllama

            # Ollama's default context window is small; our SQL prompt must not be truncated.
            return ChatOllama(model=spec.model, base_url=s.ollama_base_url, num_ctx=16384)
        if p == "anthropic":
            if not api_key:
                return None
            from langchain_anthropic import ChatAnthropic

            # No temperature/top_p (rejected by current Claude models); structured output
            # uses json_schema, never forced tool_choice.
            return ChatAnthropic(
                model=spec.model,
                api_key=api_key,
                max_tokens=16000,
                timeout=s.llm_timeout_s,
                max_retries=1,
            )
        raise ValueError(f"Unknown LLM provider {p!r} in {spec.label}")
