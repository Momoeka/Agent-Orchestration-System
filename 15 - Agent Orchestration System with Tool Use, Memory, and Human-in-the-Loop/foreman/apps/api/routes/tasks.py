from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from apps.api.controllers import tasks as controller
from apps.api.controllers.tasks import CreateTaskRequest, CreateTaskResponse
from apps.api.services.task_service import TaskService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def get_service(request: Request) -> TaskService:
    return request.app.state.task_service  # type: ignore[no-any-return]


Service = Annotated[TaskService, Depends(get_service)]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateTaskResponse)
def create_task(body: CreateTaskRequest, service: Service) -> CreateTaskResponse:
    return controller.create_task(service, body)


@router.get("")
def list_tasks(service: Service, limit: int = 50) -> list[dict[str, Any]]:
    return controller.list_tasks(service, limit)


@router.get("/{task_id}")
def get_task(task_id: str, service: Service) -> dict[str, Any]:
    return controller.get_task(service, task_id)
