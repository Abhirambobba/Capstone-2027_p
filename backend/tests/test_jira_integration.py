from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.jira_oauth_service import (
    JiraOAuthCallbackResult,
    JiraOAuthService,
    InMemoryJiraTokenService,
)

client = TestClient(app)


@pytest.fixture
def jira_service(monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "jira-client")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "jira-secret")
    monkeypatch.setenv("JIRA_CALLBACK_URL", "http://localhost:3000/api/integrations/jira/callback")
    token_service = InMemoryJiraTokenService()
    service = JiraOAuthService(token_service=token_service)

    def fake_post(url, data=None, headers=None, timeout=None):
        assert url == "https://auth.atlassian.com/oauth/token"
        assert data["client_id"] == "jira-client"
        assert data["client_secret"] == "jira-secret"
        assert data["redirect_uri"] == "http://localhost:3000/api/integrations/jira/callback"
        return Mock(
            status_code=200,
            json=lambda: {
                "access_token": "atlas-access",
                "refresh_token": "atlas-refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
            raise_for_status=lambda: None,
        )

    def fake_get(url, headers=None, timeout=None):
        assert url == "https://api.atlassian.com/oauth/token/accessible-resources"
        assert headers["Authorization"] == "Bearer atlas-access"
        return Mock(
            status_code=200,
            json=lambda: [{"id": "cloud-123", "name": "Astra Cloud", "url": "https://astra.atlassian.net"}],
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    return service


def test_jira_callback_requires_state():
    service = JiraOAuthService(token_service=InMemoryJiraTokenService())
    with pytest.raises(ValueError, match="state"):
        service.handle_callback("EMP_001", code="abc123", state=None, redirect_uri="http://localhost:3000/api/integrations/jira/callback")


def test_jira_callback_rejects_invalid_state():
    service = JiraOAuthService(token_service=InMemoryJiraTokenService())
    with pytest.raises(ValueError, match="state"):
        service.handle_callback(
            "EMP_001",
            code="abc123",
            state="invalid",
            redirect_uri="http://localhost:3000/api/integrations/jira/callback",
            expected_state="expected-state",
        )


def test_jira_callback_rejects_access_denied():
    service = JiraOAuthService(token_service=InMemoryJiraTokenService())
    with pytest.raises(ValueError, match="denied"):
        service.handle_callback(
            "EMP_001",
            code="",
            state="state-123",
            redirect_uri="http://localhost:3000/api/integrations/jira/callback",
            error="access_denied",
        )


def test_jira_callback_handles_exchange_error(monkeypatch):
    service = JiraOAuthService(token_service=InMemoryJiraTokenService())
    sensitive_response = '"access_token":"callback-access-secret","refresh_token":"callback-refresh-secret","client_secret":"callback-client-secret"'

    def fake_post(*args, **kwargs):
        return Mock(status_code=400, text=sensitive_response, json=lambda: {"error": "invalid_grant"})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="token exchange") as raised:
        service.handle_callback(
            "EMP_001",
            code="code-123",
            state="state-123",
            redirect_uri="http://localhost:3000/api/integrations/jira/callback",
        )
    assert "status=400" in str(raised.value)
    assert "callback-access-secret" not in str(raised.value)
    assert "callback-refresh-secret" not in str(raised.value)
    assert "callback-client-secret" not in str(raised.value)


def test_jira_projects_error_does_not_expose_provider_response(monkeypatch):
    token_service = InMemoryJiraTokenService()
    token_service.store("EMP_001", "cloud-123", "stored-access-secret", "stored-refresh-secret", 3600)
    service = JiraOAuthService(token_service=token_service)
    sensitive_response = '"access_token":"provider-access-secret","refresh_token":"provider-refresh-secret","Authorization":"Bearer provider-header-secret"'

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: Mock(status_code=401, text=sensitive_response),
    )

    with pytest.raises(RuntimeError, match="status=401") as raised:
        service.list_projects_for_employee("EMP_001")
    for secret in (
        "provider-access-secret",
        "provider-refresh-secret",
        "provider-header-secret",
    ):
        assert secret not in str(raised.value)


def test_jira_callback_handles_accessible_resources_error(monkeypatch):
    service = JiraOAuthService(token_service=InMemoryJiraTokenService())

    def fake_post(*args, **kwargs):
        return Mock(
            status_code=200,
            json=lambda: {"access_token": "atlas-access", "refresh_token": "atlas-refresh", "expires_in": 3600},
        )

    def fake_get(*args, **kwargs):
        return Mock(status_code=500, json=lambda: {"error": "unable to fetch resources"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(RuntimeError, match="accessible resources"):
        service.handle_callback(
            "EMP_001",
            code="code-123",
            state="state-123",
            redirect_uri="http://localhost:3000/api/integrations/jira/callback",
        )


def test_jira_callback_success_returns_safe_metadata(jira_service):
    result = jira_service.handle_callback(
        "EMP_001",
        code="code-123",
        state="state-123",
        redirect_uri="http://localhost:3000/api/integrations/jira/callback",
    )

    assert result["connected"] is True
    assert result["cloud_id"] == "cloud-123"
    assert result["site_name"] == "Astra Cloud"
    assert result["base_url"] == "https://astra.atlassian.net"
    assert "access_token" not in result
    assert "refresh_token" not in result
    stored_connection = jira_service.token_service.get("EMP_001", "cloud-123")
    assert stored_connection["access_token"] == "atlas-access"
    assert stored_connection["refresh_token"] == "atlas-refresh"
    assert stored_connection["expires_in"] == 3600


def test_default_jira_services_share_token_store():
    callback_service = JiraOAuthService()
    projects_service = JiraOAuthService()

    assert callback_service.token_service is projects_service.token_service


def test_jira_projects_uses_stored_token_and_cloud_url(monkeypatch):
    token_service = InMemoryJiraTokenService()
    token_service.store("EMP_001", "cloud-from-token-store", "access-from-token-store", "refresh", 3600)
    service = JiraOAuthService(token_service=token_service)

    def fake_get(url, headers=None, timeout=None):
        assert url == "https://api.atlassian.com/ex/jira/cloud-from-token-store/rest/api/3/project"
        assert headers == {"Authorization": "Bearer access-from-token-store"}
        return Mock(status_code=200, json=lambda: [{"id": "10001", "key": "KAN", "name": "Astra"}])

    monkeypatch.setattr(httpx, "get", fake_get)

    projects = service.list_projects_for_employee("EMP_001")
    assert projects == [{
        "id": "10001",
        "key": "KAN",
        "name": "Astra",
        "description": None,
        "project_type": None,
    }]


def test_jira_callback_route_rejects_missing_state_and_hides_secrets():
    response = client.post(
        "/api/v1/integrations/jira/oauth/callback",
        json={"code": "abc123"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert "state" in payload["detail"].lower()
    assert "access_token" not in str(payload)


def test_jira_callback_route_returns_success_metadata():
    class FakeJiraService:
        def get_connection_for_employee(self, employee_id):
            return None

        def list_projects_for_employee(self, employee_id):
            return []

        def handle_callback(self, employee_id, **kwargs):
            assert employee_id == "AUTH_TEST_EMPLOYEE"
            return JiraOAuthCallbackResult(
                connected=True,
                cloud_id="cloud-123",
                site_name="Astra Cloud",
                base_url="https://astra.atlassian.net",
            )

    app.dependency_overrides["jira_oauth_service_override"] = FakeJiraService()
    try:
        response = client.post(
            "/api/v1/integrations/jira/oauth/callback",
            json={"code": "code-123", "state": "state-123"},
        )
    finally:
        app.dependency_overrides.pop("jira_oauth_service_override", None)

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "cloud_id": "cloud-123",
        "site_name": "Astra Cloud",
        "base_url": "https://astra.atlassian.net",
    }


def test_jira_projects_route_requires_authentication():
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        response = client.get("/api/v1/integrations/jira/projects")
    finally:
        if original is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = original
    assert response.status_code == 401


def test_jira_projects_route_returns_authenticated_employee_projects(monkeypatch):
    class FakeJiraService:
        def get_connection_for_employee(self, employee_id):
            assert employee_id == "AUTH_TEST_EMPLOYEE"
            return {"cloud_id": "cloud-123", "access_token": "atlas-access"}

        def list_projects_for_employee(self, employee_id):
            assert employee_id == "AUTH_TEST_EMPLOYEE"
            return [
                {
                    "id": "10001",
                    "key": "KAN",
                    "name": "Astra Engineering",
                    "description": "Engineering project",
                    "project_type": "software",
                }
            ]

    app.dependency_overrides["jira_oauth_service_override"] = FakeJiraService()
    monkeypatch.setattr(
        "app.api.integrations.get_jira_oauth_service",
        lambda: FakeJiraService(),
    )

    response = client.get("/api/v1/integrations/jira/projects")
    assert response.status_code == 200
    assert response.json()["projects"][0]["key"] == "KAN"
    assert "atlas-access" not in response.text
    app.dependency_overrides.pop("jira_oauth_service_override", None)


def test_jira_projects_route_hides_tokens_in_api_response(monkeypatch):
    class FakeJiraService:
        def get_connection_for_employee(self, employee_id):
            return {"cloud_id": "cloud-123", "access_token": "atlas-access"}

        def list_projects_for_employee(self, employee_id):
            return [{"id": "10001", "key": "KAN", "name": "Astra Engineering"}]

    monkeypatch.setattr(
        "app.api.integrations.get_jira_oauth_service",
        lambda: FakeJiraService(),
    )

    response = client.get("/api/v1/integrations/jira/projects")
    assert response.status_code == 200
    assert "atlas-access" not in response.text
    assert "atlas-refresh" not in response.text
