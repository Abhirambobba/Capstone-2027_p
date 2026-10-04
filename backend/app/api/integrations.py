from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_authenticated_user
from app.repositories.project_repository import ProjectRepository
from app.services.jira_oauth_service import (
    JiraOAuthService,
    get_jira_oauth_service,
)
from app.core.dynamodb import get_projects_table

router = APIRouter()


def get_project_repository() -> ProjectRepository:
    return ProjectRepository(get_projects_table())


def resolve_jira_oauth_service() -> JiraOAuthService:
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None

    override = None if app is None else app.dependency_overrides.get(get_jira_oauth_service)
    if override is None and app is not None:
        override = app.dependency_overrides.get("jira_oauth_service_override")
    if override is not None:
        candidate = override() if callable(override) else override
        if hasattr(candidate, "get_connection_for_employee") and hasattr(candidate, "list_projects_for_employee"):
            return candidate
    return get_jira_oauth_service()


class JiraOAuthCallbackRequest(BaseModel):
    code: str | None = None
    state: str | None = None
    redirect_uri: str | None = None
    error: str | None = None


class JiraOAuthCallbackResponse(BaseModel):
    connected: bool = True
    cloud_id: str
    site_name: str | None = None
    base_url: str | None = None


class JiraProjectListResponse(BaseModel):
    projects: list[dict[str, Any]] = []


class JiraProjectIssueResponse(BaseModel):
    project: dict[str, Any]
    issues: list[dict[str, Any]]


@router.post(
    "/api/v1/integrations/jira/oauth/callback",
    response_model=JiraOAuthCallbackResponse,
)
def jira_oauth_callback(
    request: JiraOAuthCallbackRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: JiraOAuthService = Depends(resolve_jira_oauth_service),
) -> JiraOAuthCallbackResponse:
    try:
        result = service.handle_callback(
            current_user.employee_id,
            code=request.code,
            state=request.state,
            redirect_uri=request.redirect_uri,
            error=request.error,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return JiraOAuthCallbackResponse(**result)


@router.get(
    "/api/v1/integrations/jira/projects",
    response_model=JiraProjectListResponse,
    dependencies=[Depends(require_authenticated_user)],
)
def list_jira_projects(
    service: JiraOAuthService = Depends(resolve_jira_oauth_service),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    try:
        connection = service.get_connection_for_employee(current_user.employee_id)
        if not connection:
            raise HTTPException(status_code=404, detail="Jira is not connected")
        projects = service.list_projects_for_employee(current_user.employee_id)
        return JiraProjectListResponse(projects=projects)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/jira/projects/{project_id}/issues",
    response_model=JiraProjectIssueResponse,
    dependencies=[Depends(require_authenticated_user)],
)
def list_jira_issues(
    project_id: str,
    service: JiraOAuthService = Depends(resolve_jira_oauth_service),
    project_repository: ProjectRepository = Depends(get_project_repository),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
):
    item = project_repository.get(project_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if item.get("owner_employee_id") != current_user.employee_id:
        raise HTTPException(status_code=403, detail="Project access denied")
    jira = item.get("jira")
    if not jira or jira.get("provider") != "jira":
        raise HTTPException(status_code=404, detail="Project is not connected to Jira")
    cloud_id = jira.get("cloud_id")
    project_key = jira.get("project_key")
    if not cloud_id or not project_key:
        raise HTTPException(status_code=400, detail="Project Jira metadata is incomplete")
    try:
        issues = service.list_issues_for_project(
            current_user.employee_id,
            cloud_id=cloud_id,
            project_key=project_key,
        )
        return JiraProjectIssueResponse(
            project={
                "astra_project_id": project_id,
                "jira_project_id": jira.get("project_id"),
                "jira_project_key": project_key,
            },
            issues=issues,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
