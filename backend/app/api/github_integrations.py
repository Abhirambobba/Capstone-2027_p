from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_authenticated_user, require_manager
from app.core.dynamodb import (
    get_employees_table,
    get_projects_table,
)
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.project_repository import ProjectRepository
from app.services.github_app_service import (
    GitHubAppError,
    GitHubAppService,
    get_github_app_service,
)


router = APIRouter()


def get_employee_repository() -> EmployeeRepository:
    return EmployeeRepository(get_employees_table())


def get_project_repository() -> ProjectRepository:
    return ProjectRepository(get_projects_table())


def resolve_github_app_service() -> GitHubAppService:
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None
    override = None if app is None else app.dependency_overrides.get("github_app_service_override")
    if override is not None:
        service = override() if callable(override) else override
        if service is not None:
            return service
    return get_github_app_service()


class GitHubConnectResponse(BaseModel):
    connected: bool
    return_to: str


class GitHubRepositoryListResponse(BaseModel):
    repositories: list[dict[str, Any]]


class GitHubDebugRepository(BaseModel):
    repository_id: str
    organization: str
    repository: str
    default_branch: str
    full_name: str


class GitHubDebugRepositoryListResponse(BaseModel):
    repositories: list[GitHubDebugRepository]


class GitHubItemsResponse(BaseModel):
    items: list[dict[str, Any]]


@router.get(
    "/api/v1/integrations/github/connect",
    response_model=GitHubConnectResponse,
)
def connect_github(
    return_to: str = Query(default="/dashboard/projects"),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: GitHubAppService = Depends(resolve_github_app_service),
) -> GitHubConnectResponse:
    try:
        result = service.start_connection(current_user.employee_id, return_to)
        return GitHubConnectResponse(
            connected=result["connected"] == "true",
            return_to=result["return_to"],
        )
    except GitHubAppError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/github/repositories",
    response_model=GitHubRepositoryListResponse,
)
def list_github_repositories(
    project_id: str = Query(..., min_length=1),
    current_user: AuthenticatedUser = Depends(require_manager),
    service: GitHubAppService = Depends(resolve_github_app_service),
) -> GitHubRepositoryListResponse:
    try:
        repositories = service.list_repositories()
        return GitHubRepositoryListResponse(repositories=repositories)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/github/debug/repositories",
    response_model=GitHubDebugRepositoryListResponse,
)
def debug_list_installation_repositories(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: GitHubAppService = Depends(resolve_github_app_service),
) -> GitHubDebugRepositoryListResponse:
    try:
        repositories = service.list_repositories()
        return GitHubDebugRepositoryListResponse(repositories=repositories)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _get_project_github(
    project_id: str,
    employee_id: str,
    repository: ProjectRepository,
) -> dict[str, Any]:
    project = repository.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Astra project not found")
    if project.get("owner_employee_id") != employee_id:
        raise HTTPException(status_code=403, detail="Project access denied")
    github = project.get("github")
    if not isinstance(github, dict) or github.get("provider") != "github":
        raise HTTPException(status_code=404, detail="Astra project is not connected to GitHub")
    return github


def _assert_project_access(
    github: dict[str, Any],
    service: GitHubAppService,
) -> None:
    try:
        service.verify_project_repository(github)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/github/projects/{astra_project_id}/pulls",
    response_model=GitHubItemsResponse,
)
def list_github_pull_requests(
    astra_project_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: GitHubAppService = Depends(resolve_github_app_service),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    project_repository: ProjectRepository = Depends(get_project_repository),
) -> GitHubItemsResponse:
    github = _get_project_github(astra_project_id, current_user.employee_id, project_repository)
    _assert_project_access(github, service)
    try:
        items = service.pull_requests(current_user.employee_id, github, employee_repository)
        return GitHubItemsResponse(items=items)
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/github/projects/{astra_project_id}/commits",
    response_model=GitHubItemsResponse,
)
def list_github_commits(
    astra_project_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: GitHubAppService = Depends(resolve_github_app_service),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    project_repository: ProjectRepository = Depends(get_project_repository),
) -> GitHubItemsResponse:
    github = _get_project_github(astra_project_id, current_user.employee_id, project_repository)
    _assert_project_access(github, service)
    try:
        items = service.commits(current_user.employee_id, github, employee_repository)
        return GitHubItemsResponse(items=items)
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get(
    "/api/v1/integrations/github/projects/{astra_project_id}/issues",
    response_model=GitHubItemsResponse,
)
def list_github_issues(
    astra_project_id: str,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: GitHubAppService = Depends(resolve_github_app_service),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    project_repository: ProjectRepository = Depends(get_project_repository),
) -> GitHubItemsResponse:
    github = _get_project_github(astra_project_id, current_user.employee_id, project_repository)
    _assert_project_access(github, service)
    try:
        items = service.issues(current_user.employee_id, github, employee_repository)
        return GitHubItemsResponse(items=items)
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


