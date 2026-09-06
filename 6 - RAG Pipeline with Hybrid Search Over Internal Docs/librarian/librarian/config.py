"""Typed settings — the ONLY module that reads environment variables (Rules.md §4)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- embeddings (one model per index; switching models means a new collection) ---
    embedding_provider: str = "ollama"  # ollama | gemini
    embedding_model: str = "nomic-embed-text"
    ollama_base_url: str = "http://localhost:11434/v1"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    # --- chat providers (generator / judge role chains, config/models.yaml) ---
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    enable_paid_providers: bool = False
    explabs_api_key: str = ""
    explabs_base_url: str = "https://api.experientiallabs.ai/v1"
    models_config_path: Path = Path("config/models.yaml")

    # --- answering (Phase 3) ---
    answer_context_k: int = 5
    answer_max_tokens: int = 1200
    answer_confidence_threshold: float = 0.55

    # --- stores ---
    chroma_host: str = ""  # empty = local persistent client at chroma_path (zero infra)
    chroma_port: int = 8001
    chroma_path: Path = Path("./data/chroma")
    store_path: Path = Path("./data/chunks.db")
    corpus_dir: Path = Path("./data/corpus")

    # --- retrieval knobs (PRD §10: tuned against the golden set, not by taste) ---
    dedup_threshold: float = 0.95
    dense_k: int = 10
    sparse_k: int = 10
    fuse_keep: int = 20
    rrf_k: int = 60
    weight_dense: float = 0.7
    weight_sparse: float = 0.3
    reranker: str = "cross-encoder"  # cross-encoder | none
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # --- chunking defaults (bake-off in Phase 4 may change them) ---
    chunk_strategy: str = "heading"
    chunk_size: int = 1200
    chunk_overlap: int = 200


    def env_value(self, env_name: str) -> str:
        """Resolve a value by its env-variable name (e.g. ``MISTRAL_API_KEY``) so
        config/models.yaml can reference env names without anything reading os.environ."""
        attr = env_name.lower()
        if not hasattr(self, attr):
            raise KeyError(f"Unknown setting referenced by models config: {env_name}")
        value = getattr(self, attr)
        return "" if value is None else str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
