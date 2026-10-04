from datetime import datetime, timezone

from app.models.project import (
    ProjectCreateRequest,
    ProjectPage,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.jira_oauth_service import JiraOAuthService, get_jira_oauth_service
from app.repositories.employee_repository import (
    EmployeeRepository,
    EmployeeRepositoryValidationError,
    EmployeeTableUnavailableError,
)
from app.repositories.project_repository import ProjectRepository


class ProjectNotFoundError(Exception):
    pass


class ProjectOwnerNotFoundError(Exception):
    pass


class ProjectEmployeeTableUnavailableError(Exception):
    pass


class ProjectEmployeeValidationError(Exception):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProjectService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        employee_repository: EmployeeRepository,
        jira_service: JiraOAuthService | None = None,
    ):
        self.project_repository = project_repository
        self.employee_repository = employee_repository
        self.jira_service = jira_service or get_jira_oauth_service()

    def create(
        self, request: ProjectCreateRequest, employee_id: str | None = None
    ) -> ProjectResponse:
        self._verify_owner(request.owner_employee_id)
        request = self._normalize_jira_metadata(request, employee_id)
        timestamp = utc_timestamp()
        item = request.model_dump(mode="json", exclude_none=True)
        item.update({"created_at": timestamp, "updated_at": timestamp})
        return ProjectResponse.model_validate(self.project_repository.create(item))

    def get(self, project_id: str) -> ProjectResponse:
        item = self.project_repository.get(project_id)
        if item is None:
            raise ProjectNotFoundError
        return ProjectResponse.model_validate(item)

    def list(self, limit: int, next_token: str | None) -> ProjectPage:
        start_key = self.project_repository.decode_key(next_token)
        items, last_key = self.project_repository.list(limit, start_key)
        return ProjectPage(
            items=[ProjectResponse.model_validate(item) for item in items],
            next_token=self.project_repository.encode_key(last_key),
        )

    def update(
        self, project_id: str, request: ProjectUpdateRequest, employee_id: str | None = None
    ) -> ProjectResponse:
        fields = request.model_dump(exclude_unset=True, mode="json")
        if "owner_employee_id" in fields:
            self._verify_owner(fields["owner_employee_id"])
        if "jira" in fields:
            request = self._normalize_jira_metadata(request, employee_id)
            fields = request.model_dump(exclude_unset=True, mode="json")
        fields["updated_at"] = utc_timestamp()
        item = self.project_repository.update(project_id, fields)
        if item is None:
            raise ProjectNotFoundError
        return ProjectResponse.model_validate(item)

    def _normalize_jira_metadata(self, request, employee_id: str | None):
        if request.jira is None:
            return request
        jira_payload = request.jira.model_dump(exclude_none=True, mode="json")
        if not jira_payload:
            return request
        if employee_id is None:
            return request

        jira_service = self.jira_service
        try:
            from app.main import app
        except Exception:  # pragma: no cover - import-time safety for isolated contexts
            app = None

        if app is not None:
            override = app.dependency_overrides.get("jira_oauth_service_override")
            if override is not None:
                resolved = override() if callable(override) else override
                if hasattr(resolved, "get_connection_for_employee") and hasattr(resolved, "list_projects_for_employee"):
                    jira_service = resolved

        company_jira = jira_service.get_connection_for_employee(employee_id)
        if not company_jira:
            return request
        cloud_id = company_jira.get("cloud_id")
        if not cloud_id:
            raise ValueError("Jira connection is incomplete")
        project_id = jira_payload.get("project_id")
        project_key = jira_payload.get("project_key")
        if project_id or project_key:
            if not project_id or not project_key:
                raise ValueError("Both Jira project_id and project_key are required")
            projects = jira_service.list_projects_for_employee(employee_id)
            project_match = next(
                (
                    item
                    for item in projects
                    if str(item.get("id")) == str(project_id)
                    and str(item.get("key")) == str(project_key)
                ),
                None,
            )
            if project_match is None:
                raise ValueError("Selected Jira project was not found in the connected Jira cloud")
        jira_payload["cloud_id"] = cloud_id
        jira_payload["provider"] = "jira"
        request.jira = type(request.jira)(**jira_payload)
        return request

    def _verify_owner(self, employee_id: str | None) -> None:
        if employee_id is None:
            return
        try:
            employee = self.employee_repository.get(employee_id)
        except EmployeeTableUnavailableError as error:
            raise ProjectEmployeeTableUnavailableError from error
        except EmployeeRepositoryValidationError as error:
            raise ProjectEmployeeValidationError(str(error)) from error
        if employee is None:
            raise ProjectOwnerNotFoundError
