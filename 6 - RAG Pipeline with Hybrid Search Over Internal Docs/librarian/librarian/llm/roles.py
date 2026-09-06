"""Load and validate config/models.yaml. Paid chain entries are dropped unless enabled
(Rules.md §2) — the same semantics Foreman proved: one YAML serves both the $0 default and
the credit-backed setup, and a role whose chain would end up empty is an error."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ChainEntry(BaseModel):
    provider: str
    model: str


class RoleConfig(BaseModel):
    chain: list[ChainEntry] = Field(min_length=1)


class ProviderConfig(BaseModel):
    base_url_env: str
    api_key_env: str | None = None
    free_tier: bool = False
    paid: bool = False


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise KeyError(f"No role '{name}' in models config (have: {sorted(self.roles)})")
        return self.roles[name]


def load_models_config(path: Path, *, enable_paid: bool = False) -> ModelsConfig:
    cfg = ModelsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for role_name, role in cfg.roles.items():
        for entry in role.chain:
            if entry.provider not in cfg.providers:
                raise ValueError(
                    f"Role '{role_name}' references unknown provider '{entry.provider}'"
                )
        if not enable_paid:
            kept = [e for e in role.chain if not cfg.providers[e.provider].paid]
            if not kept:
                raise ValueError(
                    f"Role '{role_name}' has only paid providers in its chain "
                    "but ENABLE_PAID_PROVIDERS is false"
                )
            role.chain = kept
    return cfg
