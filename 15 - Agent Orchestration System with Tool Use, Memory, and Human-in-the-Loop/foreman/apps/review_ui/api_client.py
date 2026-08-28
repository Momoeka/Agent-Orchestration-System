"""Thin HTTP client the operator UI uses. The UI never touches the database directly."""

from __future__ import annotations

from typing import Any

import httpx


class ForemanClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 20.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_s,
        )

    def approvals(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        r = self._client.get("/v1/approvals", params=params)
        r.raise_for_status()
        return list(r.json())

    def approval(self, approval_id: int) -> dict[str, Any]:
        r = self._client.get(f"/v1/approvals/{approval_id}")
        r.raise_for_status()
        return dict(r.json())

    def decide(
        self,
        approval_id: int,
        decision: str,
        *,
        payload: dict[str, Any] | None = None,
        reason: str = "",
        decided_by: str = "operator",
    ) -> dict[str, Any]:
        r = self._client.post(
            f"/v1/approvals/{approval_id}/decide",
            json={
                "decision": decision,
                "payload": payload or {},
                "reason": reason,
                "decided_by": decided_by,
            },
        )
        r.raise_for_status()
        return dict(r.json())

    def task(self, task_id: str) -> dict[str, Any]:
        r = self._client.get(f"/v1/tasks/{task_id}")
        r.raise_for_status()
        return dict(r.json())

    def tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        r = self._client.get("/v1/tasks", params={"limit": limit})
        r.raise_for_status()
        return list(r.json())

    def create_task(
        self, request: str, user_id: str, *, require_human_review: bool = False
    ) -> dict[str, Any]:
        r = self._client.post(
            "/v1/tasks",
            json={
                "request": request,
                "user_id": user_id,
                "require_human_review": require_human_review,
            },
        )
        r.raise_for_status()
        return dict(r.json())

    def memory_users(self) -> list[dict[str, Any]]:
        r = self._client.get("/v1/memory/users")
        r.raise_for_status()
        return list(r.json())

    def memories(self, user_id: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/v1/memory/users/{user_id}")
        r.raise_for_status()
        return list(r.json())

    def delete_memories(self, user_id: str) -> dict[str, Any]:
        r = self._client.delete(f"/v1/memory/users/{user_id}")
        r.raise_for_status()
        return dict(r.json())

    def outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        r = self._client.get("/v1/outbox", params={"limit": limit})
        r.raise_for_status()
        return list(r.json())

    def health(self) -> bool:
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False
