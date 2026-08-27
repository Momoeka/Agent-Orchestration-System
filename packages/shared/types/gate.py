from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from packages.shared.types.tools import RiskClass


class GateAction(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"
    BLOCK = "block"


class Decision(BaseModel):
    action: GateAction
    reason: str
    risk: RiskClass | None = None
    latency_ms: int = 0
