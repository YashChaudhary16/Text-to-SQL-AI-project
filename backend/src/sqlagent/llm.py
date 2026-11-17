"""Provider-agnostic LLM access with ordered fallbacks.

Each graph node asks for a *role* ("router" or "sql"), never a provider. The role maps to
an ordered chain of "provider:model" specs; providers without credentials are skipped,
and on any error (rate limit, quota, timeout, unparseable output) the next one is tried.

Claude sits in the chain in one of three ways:
  * the visitor supplied their own key (BYO)  -> Claude first, for that request only
  * escalation after repeated SQL failures    -> Claude first, if a key is available
  * owner key explicitly enabled              -> Claude last, as the final fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from sqlagent.config import Settings

log = logging.getLogger(__name__)

Role = Literal["router", "sql"]
T = TypeVar("T", bound=BaseModel)

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
    ) -> tuple[T, CallRecord]:
        async def call(model: BaseChatModel) -> tuple[T, Any]:
            runnable = model.with_structured_output(schema, method="json_schema", include_raw=True)
            out = await runnable.ainvoke(messages)
            if out.get("parsed") is None:
                raise ValueError(f"Unparseable structured output: {out.get('parsing_error')}")
            return out["parsed"], out["raw"]

        return await self._run(role, call, byo_key=byo_key, escalate=escalate)

    async def text(
        self, role: Role, messages: list[BaseMessage], *, byo_key: str | None = None
    ) -> tuple[str, CallRecord]:
        async def call(model: BaseChatModel) -> tuple[str, Any]:
            msg = await model.ainvoke(messages)
            text = msg.text if isinstance(msg.text, str) else msg.text()
            if not text.strip():
                raise ValueError("Empty response")
            return text.strip(), msg

        return await self._run(role, call, byo_key=byo_key)

    def available_models(self, role: Role) -> list[str]:
        return [label for label, _ in self._candidates(role, byo_key=None, escalate=False)]

    # --- internals -----------------------------------------------------------------------
    async def _run(self, role: Role, call, *, byo_key: str | None, escalate: bool = False):
        failed: list[str] = []
        errors: list[str] = []
        for label, model in self._candidates(role, byo_key=byo_key, escalate=escalate):
            try:
                result, raw = await call(model)
            except Exception as e:  # noqa: BLE001 - any provider failure means "try the next"
                log.warning("LLM %s failed (%s): %s", label, role, e)
                failed.append(label)
                errors.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")
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

        order: list[tuple[ModelSpec, str | None]] = []
        if claude_key and (byo_key or escalate):
            order.append((claude, claude_key))
        order += [(spec, None) for spec in self._chains[role]]
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

            return ChatGroq(model=spec.model, api_key=s.groq_api_key, timeout=s.llm_timeout_s, max_retries=1)
        if p in OPENAI_COMPATIBLE:
            base_url, key_attr = OPENAI_COMPATIBLE[p]
            key = getattr(s, key_attr)
            if not key:
                return None
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=spec.model, base_url=base_url, api_key=key, timeout=s.llm_timeout_s, max_retries=1
            )
        if p == "ollama":
            if not s.ollama_base_url:
                return None
            from langchain_ollama import ChatOllama

            return ChatOllama(model=spec.model, base_url=s.ollama_base_url)
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
