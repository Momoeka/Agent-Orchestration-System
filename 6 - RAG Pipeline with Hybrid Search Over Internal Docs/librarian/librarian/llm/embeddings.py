"""Embeddings via any OpenAI-compatible endpoint (Ollama locally, Gemini as the configured
alternative). One embedder = one vector space = one Chroma collection; there is deliberately
no silent fallback between models (Rules.md §3)."""

from __future__ import annotations

import re

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from librarian.config import Settings
from librarian.errors import NonRetryableError, RetryableError

_BATCH = 64
_SLUG = re.compile(r"[^a-zA-Z0-9._-]+")


class OpenAICompatEmbedder:
    """Satisfies the `Embedder` protocol used by chunkers, dedup, and dense retrieval."""

    def __init__(
        self,
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout_s: float = 120.0,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or "not-needed", timeout=timeout_s, max_retries=0
        )

    @property
    def space_id(self) -> str:
        """Names the vector space, e.g. ``ollama-nomic-embed-text`` — the collection suffix."""
        return _SLUG.sub("-", f"{self.provider_id}-{self.model}").strip("-").lower()

    def embed(self, texts: list[str]) -> list[list[float]]:
        label = f"{self.provider_id}/{self.model}"
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i : i + _BATCH]
            try:
                resp = self._client.embeddings.create(model=self.model, input=batch)
            except (APITimeoutError, APIConnectionError) as e:
                raise RetryableError(f"{label}: {type(e).__name__}: {e}") from e
            except RateLimitError as e:
                raise RetryableError(f"{label}: rate limited: {e}") from e
            except APIStatusError as e:
                kind = RetryableError if e.status_code >= 500 else NonRetryableError
                raise kind(f"{label}: HTTP {e.status_code}: {e}") from e
            out.extend([list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)])
        if len(out) != len(texts):
            raise RetryableError(f"{label}: got {len(out)} vectors for {len(texts)} texts")
        return out


def build_embedder(settings: Settings) -> OpenAICompatEmbedder:
    if settings.embedding_provider == "ollama":
        return OpenAICompatEmbedder(
            provider_id="ollama",
            model=settings.embedding_model,
            base_url=settings.ollama_base_url,
        )
    if settings.embedding_provider == "gemini":
        if not settings.gemini_api_key:
            raise NonRetryableError("EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set")
        return OpenAICompatEmbedder(
            provider_id="gemini",
            model=settings.embedding_model,
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
        )
    raise NonRetryableError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider!r}")
