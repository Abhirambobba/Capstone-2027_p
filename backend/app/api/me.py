from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.memberships import get_membership_service
from app.api.projects import get_project_service
from app.api.tasks import get_task_service
from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.models.project import ProjectResponse
from app.models.task import TaskListResponse, TaskStatus
from app.models.team import TeamResponse
from app.services.membership_service import (
    MembershipService,
    MembershipTeamNotFoundError,
)
from app.services.project_service import ProjectService
from app.services.task_service import TaskService

router = APIRouter()


class ActivityEvent(BaseModel):
    activity_id: str
    project_id: str
    task_id: str
    title: str
    status: TaskStatus
    occurred_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityEvent]
    next_token: str | None = None


@router.get("/api/v1/me")
def get_me(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    return current_user.employee


@router.get("/api/v1/me/tasks", response_model=TaskListResponse)
def get_my_tasks(
    limit: int = Query(default=25, ge=1, le=100),
    next_token: str | None = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: TaskService = Depends(get_task_service),
):
    return service.list_employee_tasks(
        current_user.employee_id, limit, next_token, None, None, None
    )


@router.get("/api/v1/me/projects", response_model=list[ProjectResponse])
def get_my_projects(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    memberships: MembershipService = Depends(get_membership_service),
    projects: ProjectService = Depends(get_project_service),
):
    employee_memberships = memberships.list_employee_projects(current_user.employee_id)
    return [projects.get(membership.project_id) for membership in employee_memberships]


@router.get("/api/v1/me/teams", response_model=list[TeamResponse])
def get_my_teams(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    memberships: MembershipService = Depends(get_membership_service),
):
    employee_memberships = memberships.list_employee_projects(current_user.employee_id)
    teams = []
    for membership in employee_memberships:
        if not membership.team_id:
            continue
        try:
            teams.append(
                memberships.get_team(membership.project_id, membership.team_id)
            )
        except MembershipTeamNotFoundError:
            continue
    return teams


@router.get("/api/v1/me/activity", response_model=ActivityPage)
def get_my_activity(
    limit: int = Query(default=25, ge=1, le=100),
    next_token: str | None = None,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    tasks: TaskService = Depends(get_task_service),
):
    task_page = tasks.list_employee_tasks(
        current_user.employee_id, limit, next_token, None, None, None
    )
    events = [
        ActivityEvent(
            activity_id=f"TASK#{task.task_id}#{task.last_activity_at.isoformat()}",
            project_id=task.project_id,
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            occurred_at=task.last_activity_at,
        )
        for task in task_page.items
        if task.last_activity_at is not None
    ]
    return ActivityPage(items=events, next_token=task_page.next_token)