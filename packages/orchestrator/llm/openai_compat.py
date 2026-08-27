"""One provider class for every OpenAI-compatible endpoint (TokenRouter, Mistral, Groq, Gemini, Ollama).

Normalises: tool calls → `ToolCall`, JSON-schema output (with strict `additionalProperties: false`
injected — Groq and OpenAI require it), usage → `Usage`, and provider exceptions → the Foreman error
taxonomy so the fallback chain can decide what to do.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from packages.shared.errors import NonRetryableError, RetryableError
from packages.shared.types.llm import LLMMessage, LLMResponse, Usage
from packages.shared.types.tools import ToolCall


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with ``additionalProperties: false`` on every object node.

    Strict json_schema modes (OpenAI, Groq) reject schemas without it; Pydantic does not emit it.
    """
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("additionalProperties", False)
            for key in ("properties", "$defs", "definitions"):
                if isinstance(node.get(key), dict):
                    for child in node[key].values():
                        walk(child)
            for key in ("items", "additionalProperties"):
                if isinstance(node.get(key), dict):
                    walk(node[key])
            for key in ("anyOf", "oneOf", "allOf"):
                if isinstance(node.get(key), list):
                    for child in node[key]:
                        walk(child)

    walk(out)
    return out


def _parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"arguments were not valid JSON: {e.msg} at position {e.pos}"
    if not isinstance(parsed, dict):
        return {}, "arguments must be a JSON object"
    return parsed, None


class OpenAICompatProvider:
    def __init__(
        self,
        provider_id: str,
        base_url: str,
        api_key: str,
        *,
        default_timeout_s: float = 60.0,
        supports_effort: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.supports_effort = supports_effort
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            timeout=default_timeout_s,
            max_retries=0,  # the chain decides about retries and fallbacks, not the SDK
        )

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "Response",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        effort: str | None = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": strict_schema(response_schema),
                    "strict": True,
                },
            }
        if effort and self.supports_effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s

        started = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            # Some providers accept json_object but not json_schema; degrade once, still validated upstream.
            if response_schema is not None and "response_format" in str(e).lower():
                kwargs["response_format"] = {"type": "json_object"}
                try:
                    resp = await self._client.chat.completions.create(**kwargs)
                except APIStatusError as e2:
                    raise _map_status_error(e2) from e2
            else:
                raise NonRetryableError(f"{self.provider_id}/{model}: bad request: {e}") from e
        except (APITimeoutError, APIConnectionError) as e:
            raise RetryableError(f"{self.provider_id}/{model}: {type(e).__name__}: {e}") from e
        except RateLimitError as e:
            raise RetryableError(f"{self.provider_id}/{model}: rate limited: {e}") from e
        except (AuthenticationError, PermissionDeniedError, NotFoundError) as e:
            raise NonRetryableError(f"{self.provider_id}/{model}: {type(e).__name__}: {e}") from e
        except APIStatusError as e:
            raise _map_status_error(e, f"{self.provider_id}/{model}") from e
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not resp.choices:
            raise RetryableError(f"{self.provider_id}/{model}: empty choices in response")
        choice = resp.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            args, parse_error = _parse_arguments(tc.function.arguments)
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=args, parse_error=parse_error)
            )

        usage = Usage()
        if resp.usage is not None:
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
            )

        raw = message.model_dump(exclude_none=True)
        raw.pop("function_call", None)  # legacy field; never send it back
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            provider=self.provider_id,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            raw_assistant_message=raw,
        )


def _map_status_error(e: APIStatusError, label: str = "") -> Exception:
    prefix = f"{label}: " if label else ""
    if e.status_code == 429 or e.status_code >= 500:
        return RetryableError(f"{prefix}HTTP {e.status_code}: {e}")
    return NonRetryableError(f"{prefix}HTTP {e.status_code}: {e}")
