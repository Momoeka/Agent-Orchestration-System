"""The one interface every model call goes through (Architecture.md §10)."""

from __future__ import annotations

from typing import Any, Protocol

from packages.shared.types.llm import LLMMessage, LLMResponse


class LLMClient(Protocol):
    provider_id: str

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
    ) -> LLMResponse: ...
