"""Chat via any OpenAI-compatible endpoint, with a per-role fallback chain.

Two lessons from Foreman's live runs are baked in: some routes (gpt-6-astra, o-series) reject
`temperature` — drop it once and retry; some providers return list-of-parts message content —
flatten to text. Free tiers 429/503 constantly, so the chain moves on and backs off.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from librarian.config import Settings
from librarian.errors import NonRetryableError, RetryableError
from librarian.llm.roles import load_models_config

log = logging.getLogger(__name__)
BACKOFF_SECONDS = (3.0, 8.0)


class Chat(Protocol):
    """What the answer layer needs from a chat model; `ChainedChat` satisfies it, tests fake it."""

    last_used: str

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1200,
        json_object: bool = False,
    ) -> str: ...


def text_content(raw: Any) -> str:
    """message.content as plain text (providers may return a list of content parts)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(raw)


class ChatProvider:
    def __init__(
        self, provider_id: str, base_url: str, api_key: str, *, timeout_s: float = 120.0
    ) -> None:
        self.provider_id = provider_id
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or "not-needed", timeout=timeout_s, max_retries=0
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int = 1200,
        temperature: float = 0.2,
        json_object: bool = False,
    ) -> str:
        label = f"{self.provider_id}/{model}"
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_object:
            kwargs["response_format"] = {"type": "json_object"}
        for _ in range(3):  # degrade at most twice: temperature, then response_format
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                reason = str(e).lower()
                if "temperature" in reason and "temperature" in kwargs:
                    kwargs.pop("temperature")
                    continue
                if "response_format" in reason and "response_format" in kwargs:
                    kwargs.pop("response_format")
                    continue
                raise NonRetryableError(f"{label}: bad request: {e}") from e
            except (APITimeoutError, APIConnectionError) as e:
                raise RetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except RateLimitError as e:
                raise RetryableError(f"{label}: rate limited: {e}") from e
            except (AuthenticationError, PermissionDeniedError, NotFoundError) as e:
                raise NonRetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except APIStatusError as e:
                kind = RetryableError if e.status_code >= 500 else NonRetryableError
                raise kind(f"{label}: HTTP {e.status_code}: {e}") from e
            if not resp.choices:
                raise RetryableError(f"{label}: empty choices")
            return text_content(resp.choices[0].message.content)
        raise NonRetryableError(f"{label}: bad request persisted after degrading parameters")


class ChainedChat:
    """Try each (provider, model) entry in order; back off when a whole round fails."""

    def __init__(
        self,
        role: str,
        entries: list[tuple[ChatProvider, str]],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not entries:
            raise NonRetryableError(f"role '{role}' has no chain entries")
        self.role = role
        self._entries = entries
        self._sleep = sleep
        self.last_used: str = ""

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1200,
        json_object: bool = False,
    ) -> str:
        errors: list[str] = []
        for round_index in range(len(BACKOFF_SECONDS) + 1):
            for provider, model in self._entries:
                try:
                    out = provider.chat(
                        messages, model=model, max_tokens=max_tokens, json_object=json_object
                    )
                except (RetryableError, NonRetryableError) as e:
                    log.warning("chain.entry_failed role=%s error=%s", self.role, e)
                    errors.append(str(e)[:200])
                    continue
                self.last_used = f"{provider.provider_id}/{model}"
                return out
            if round_index < len(BACKOFF_SECONDS):
                self._sleep(BACKOFF_SECONDS[round_index])
        raise RetryableError(f"role '{self.role}': chain exhausted: {errors[-3:]}")


def build_chat(settings: Settings, role: str) -> ChainedChat:
    cfg = load_models_config(
        settings.models_config_path, enable_paid=settings.enable_paid_providers
    )
    providers: dict[str, ChatProvider] = {}
    entries: list[tuple[ChatProvider, str]] = []
    for entry in cfg.role(role).chain:
        pc = cfg.providers[entry.provider]
        if entry.provider not in providers:
            base_url = settings.env_value(pc.base_url_env)
            api_key = settings.env_value(pc.api_key_env) if pc.api_key_env else ""
            if not base_url or (pc.api_key_env and not api_key):
                log.warning(
                    "provider %s skipped: %s or %s not set",
                    entry.provider,
                    pc.base_url_env,
                    pc.api_key_env,
                )
                continue
            providers[entry.provider] = ChatProvider(entry.provider, base_url, api_key)
        entries.append((providers[entry.provider], entry.model))
    return ChainedChat(role, entries)
