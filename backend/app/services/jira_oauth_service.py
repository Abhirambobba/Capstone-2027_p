from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


logger = logging.getLogger(__name__)


class JiraTokenStore(Protocol):
    def store(
        self,
        employee_id: str,
        cloud_id: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        ...

    def get(self, employee_id: str, cloud_id: str) -> dict[str, Any] | None:
        ...

    def get_for_employee(self, employee_id: str) -> dict[str, Any] | None:
        ...


class InMemoryJiraTokenService:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], dict[str, Any]] = {}

    def store(
        self,
        employee_id: str,
        cloud_id: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        self._tokens[(employee_id, cloud_id)] = {
            "employee_id": employee_id,
            "cloud_id": cloud_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }

    def get(self, employee_id: str, cloud_id: str) -> dict[str, Any] | None:
        return self._tokens.get((employee_id, cloud_id))

    def get_for_employee(self, employee_id: str) -> dict[str, Any] | None:
        matches = [
            entry for (stored_employee_id, _), entry in self._tokens.items()
            if stored_employee_id == employee_id
        ]
        if not matches:
            return None
        return matches[-1]


class JiraOAuthCallbackResult(dict):
    def __init__(self, **data: Any) -> None:
        super().__init__(**data)


_DEFAULT_JIRA_TOKEN_SERVICE = InMemoryJiraTokenService()


def _is_compatible_jira_service(service: Any) -> bool:
    return (
        service is not None
        and hasattr(service, "get_connection_for_employee")
        and hasattr(service, "list_projects_for_employee")
    )


def get_jira_oauth_service() -> "JiraOAuthService":
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None

    override = None if app is None else app.dependency_overrides.get("jira_oauth_service_override")
    if override is not None:
        if callable(override):
            override = override()
        if _is_compatible_jira_service(override):
            return override
    return JiraOAuthService(token_service=_DEFAULT_JIRA_TOKEN_SERVICE)


def resolve_jira_oauth_service() -> "JiraOAuthService":
    try:
        from app.main import app
    except Exception:  # pragma: no cover - import-time safety for isolated contexts
        app = None

    override = None if app is None else app.dependency_overrides.get(get_jira_oauth_service)
    if override is not None:
        value = override() if callable(override) else override
        if _is_compatible_jira_service(value):
            return value
    return get_jira_oauth_service()


class JiraOAuthService:
    def __init__(self, token_service: JiraTokenStore | None = None) -> None:
        self.token_service = token_service or _DEFAULT_JIRA_TOKEN_SERVICE

    def get_connection_for_employee(self, employee_id: str) -> dict[str, Any] | None:
        if hasattr(self.token_service, "get_for_employee"):
            return self.token_service.get_for_employee(employee_id)
        return None

    def list_projects_for_employee(self, employee_id: str) -> list[dict[str, Any]]:
        connection = self.get_connection_for_employee(employee_id)
        logger.info(
            "Jira project lookup start: employee_id=%s has_connection=%s cloud_id=%s has_access_token=%s access_token_length=%s expires_in=%s",
            employee_id,
            connection is not None,
            connection.get("cloud_id") if connection else None,
            bool(connection and connection.get("access_token")) if connection else False,
            len(connection["access_token"]) if connection and connection.get("access_token") else 0,
            connection.get("expires_in") if connection else None,
        )
        if not connection:
            raise RuntimeError("Jira is not connected for this employee")
        access_token = connection.get("access_token")
        cloud_id = connection.get("cloud_id")
        if not access_token or not cloud_id:
            raise RuntimeError("Jira connection is incomplete")

        response = httpx.get(
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        logger.info(
            "Jira project lookup response: employee_id=%s cloud_id=%s status_code=%s",
            employee_id,
            cloud_id,
            response.status_code,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Jira project lookup failed (status={response.status_code})"
            )

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Jira project list response was not valid")

        projects: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            project_id = item.get("id")
            project_key = item.get("key")
            if not project_id or not project_key:
                continue
            projects.append(
                {
                    "id": str(project_id),
                    "key": str(project_key),
                    "name": item.get("name") or "",
                    "description": item.get("description"),
                    "project_type": item.get("projectTypeKey") or item.get("projectType"),
                }
            )
        return projects

    def list_issues_for_project(
        self,
        employee_id: str,
        *,
        cloud_id: str,
        project_key: str,
    ) -> list[dict[str, Any]]:
        connection = self.get_connection_for_employee(employee_id)
        if not connection:
            raise RuntimeError("Jira is not connected for this employee")
        access_token = connection.get("access_token")
        if not access_token:
            raise RuntimeError("Jira access token is unavailable")

        response = httpx.get(
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "jql": f"project = {project_key}",
                "fields": "summary,status,assignee",
                "maxResults": 50,
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Jira project issue lookup failed (status={response.status_code})"
            )

        payload = response.json()
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        normalized: list[dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields") or {}
            assignee = fields.get("assignee") or {}
            normalized.append(
                {
                    "id": str(issue.get("id") or ""),
                    "key": issue.get("key"),
                    "summary": fields.get("summary"),
                    "status": (fields.get("status") or {}).get("name") or "Unknown",
                    "assignee": {
                        "account_id": assignee.get("accountId"),
                        "display_name": assignee.get("displayName") or "Unassigned",
                    },
                }
            )
        return normalized

    def handle_callback(
        self,
        employee_id: str,
        *,
        code: str | None,
        state: str | None,
        redirect_uri: str | None,
        expected_state: str | None = None,
        error: str | None = None,
    ) -> JiraOAuthCallbackResult:
        if state is None or state == "":
            raise ValueError("Jira OAuth callback is missing state")
        if expected_state is not None and state != expected_state:
            raise ValueError("Jira OAuth callback state did not match")
        if error is not None:
            if error == "access_denied":
                raise ValueError("Jira authorization was denied")
            raise ValueError("Jira OAuth callback failed")
        if not code:
            raise ValueError("Jira OAuth callback is missing authorization code")

        settings = get_settings()
        if (
            not settings.jira_client_id
            or not settings.jira_client_secret
            or not settings.jira_callback_url
        ):
            raise RuntimeError("Jira OAuth is not configured")

        token_response = httpx.post(
            "https://auth.atlassian.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "code": code,
                "redirect_uri": redirect_uri or settings.jira_callback_url,
            },
            timeout=15,
        )
        logger.info(
            "Jira OAuth token exchange response: employee_id=%s status_code=%s",
            employee_id,
            token_response.status_code,
        )
        if token_response.status_code != 200:
            raise RuntimeError(
                f"Jira token exchange failed (status={token_response.status_code})"
            )

        token_payload = token_response.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("Jira token exchange did not include an access token")

        resource_response = httpx.get(
            "https://api.atlassian.com/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        logger.info(
            "Jira accessible-resources response: employee_id=%s status_code=%s",
            employee_id,
            resource_response.status_code,
        )
        if resource_response.status_code != 200:
            raise RuntimeError(
                f"Jira accessible resources lookup failed (status={resource_response.status_code})"
            )

        resources = resource_response.json()
        if not isinstance(resources, list) or not resources:
            raise RuntimeError("Jira accessible resources were not available")

        resource = resources[0]
        cloud_id = resource.get("id")
        if not cloud_id:
            raise RuntimeError("Jira cloud metadata was incomplete")

        self.token_service.store(
            employee_id=employee_id,
            cloud_id=cloud_id,
            access_token=access_token,
            refresh_token=token_payload.get("refresh_token"),
            expires_in=int(token_payload.get("expires_in") or 0) or None,
        )

        logger.info(
            "Jira OAuth callback stored token: employee_id=%s cloud_id=%s token_exists=%s token_length=%s expires_in=%s",
            employee_id,
            cloud_id,
            bool(access_token),
            len(access_token) if access_token else 0,
            int(token_payload.get("expires_in") or 0) or None,
        )

        return JiraOAuthCallbackResult(
            connected=True,
            cloud_id=cloud_id,
            site_name=resource.get("name"),
            base_url=resource.get("url"),
        )
