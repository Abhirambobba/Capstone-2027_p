import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import AuthenticatedUser, require_authenticated_user, require_manager
from app.core.dynamodb import get_employees_table, get_projects_table
from app.models.project import (
    ProjectCreateRequest,
    ProjectPage,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.project_repository import (
    ProjectAlreadyExistsError,
    ProjectRepository,
    ProjectRepositoryValidationError,
    ProjectTableUnavailableError,
)
from app.services.project_service import (
    ProjectEmployeeTableUnavailableError,
    ProjectEmployeeValidationError,
    ProjectNotFoundError,
    ProjectOwnerNotFoundError,
    ProjectService,
)
from app.services.github_app_service import get_github_app_service
from app.services.github_app_service import GitHubAppError


def resolve_project_service() -> ProjectService:
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None

    override = None if app is None else app.dependency_overrides.get(get_project_service)
    if override is not None:
        value = override() if callable(override) else override
        if isinstance(value, ProjectService):
            return value
    return get_project_service()

logger = logging.getLogger(__name__)
router = APIRouter()


def get_project_service() -> ProjectService:
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None

    jira_service = None
    if app is not None:
        override = app.dependency_overrides.get("jira_oauth_service_override")
        if override is not None:
            jira_service = override() if callable(override) else override
    return ProjectService(
        project_repository=ProjectRepository(get_projects_table()),
        employee_repository=EmployeeRepository(get_employees_table()),
        jira_service=jira_service,
        github_service=get_github_app_service(),
    )


def handle_project_repository_error(error: Exception) -> None:
    if isinstance(error, ProjectRepositoryValidationError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    if isinstance(error, ProjectTableUnavailableError):
        raise HTTPException(
            status_code=503, detail="Project database is unavailable"
        ) from error
    if isinstance(error, ProjectEmployeeTableUnavailableError):
        raise HTTPException(
            status_code=503, detail="Employee database is unavailable"
        ) from error
    if isinstance(error, ProjectEmployeeValidationError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error


@router.post(
    "/api/v1/management/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_manager)],
)
def create_project(
    request: ProjectCreateRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: ProjectService = Depends(resolve_project_service),
):
    try:
        project = service.create(request, employee_id=current_user.employee_id)
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ProjectAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="Project already exists") from error
    except ProjectOwnerNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Owner employee not found"
        ) from error
    except (
        ProjectRepositoryValidationError,
        ProjectTableUnavailableError,
        ProjectEmployeeTableUnavailableError,
        ProjectEmployeeValidationError,
    ) as error:
        handle_project_repository_error(error)
    logger.info("project_created", extra={"project_id": project.project_id})
    return project


@router.get(
    "/api/v1/management/projects/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(require_authenticated_user)],
)
def get_project(
    project_id: str,
    service: ProjectService = Depends(resolve_project_service),
):
    try:
        return service.get(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error
    except (ProjectRepositoryValidationError, ProjectTableUnavailableError) as error:
        handle_project_repository_error(error)


@router.get(
    "/api/v1/management/projects",
    response_model=ProjectPage,
    dependencies=[Depends(require_authenticated_user)],
)
def list_projects(
    limit: int = Query(default=25, ge=1, le=100),
    next_token: str | None = None,
    service: ProjectService = Depends(resolve_project_service),
):
    try:
        return service.list(limit, next_token)
    except (ProjectRepositoryValidationError, ProjectTableUnavailableError) as error:
        handle_project_repository_error(error)


@router.patch(
    "/api/v1/management/projects/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(require_manager)],
)
def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
    service: ProjectService = Depends(resolve_project_service),
):
    try:
        return service.update(project_id, request, employee_id=current_user.employee_id)
    except GitHubAppError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error
    except ProjectOwnerNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Owner employee not found"
        ) from error
    except (
        ProjectRepositoryValidationError,
        ProjectTableUnavailableError,
        ProjectEmployeeTableUnavailableError,
        ProjectEmployeeValidationError,
    ) as error:
        handle_project_repository_error(error)
