from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.github_app_service import GitHubAppError, GitHubAppService


client = TestClient(app)


@pytest.fixture
def github_service(monkeypatch):
    monkeypatch.setenv("DYNAMODB_EMPLOYEES_TABLE", "AstraEmployees")
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "github-pat-server-only")
    monkeypatch.setenv("GITHUB_FRONTEND_URL", "http://localhost:3000")
    return GitHubAppService()


def response(url: str, payload, status_code: int = 200):
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_github_connection_verifies_backend_pat(github_service):
    github_service._github_get = lambda path: {"login": "souriesh"}

    assert github_service.start_connection("EMP_001", "/dashboard/projects/123456") == {
        "connected": "true",
        "return_to": "/dashboard/projects/123456",
    }


def test_github_repository_discovery_uses_user_repositories_and_server_pat(github_service, monkeypatch):
    def fake_request(method, url, headers=None, **kwargs):
        assert method == "GET"
        assert "/user/repos" in url
        assert headers["Authorization"] == "Bearer github-pat-server-only"
        return response(url, [{
            "id": 1142228151,
            "name": "MCP-Capstone",
            "full_name": "souriesh/MCP-Capstone",
            "default_branch": "main",
            "owner": {"login": "souriesh"},
        }])

    monkeypatch.setattr(httpx, "request", fake_request)
    repositories = github_service.list_repositories()

    assert repositories == [{
        "repository_id": "1142228151",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "full_name": "souriesh/MCP-Capstone",
        "default_branch": "main",
    }]
    assert "github-pat-server-only" not in str(repositories)


def test_github_repository_selection_validates_normalized_repository(github_service):
    github_service.list_repositories = lambda: [{
        "repository_id": "1142228151",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "default_branch": "main",
    }]

    assert github_service.resolve_repository("1142228151") == {
        "repository_id": "1142228151",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "default_branch": "main",
    }


def test_github_repository_selection_rejects_unavailable_repository(github_service):
    github_service.list_repositories = lambda: []

    with pytest.raises(ValueError, match="configured GitHub account"):
        github_service.resolve_repository("1142228151")


def test_github_repository_endpoint_requires_project_id(monkeypatch):
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        result = client.get("/api/v1/integrations/github/repositories")
    finally:
        if original is not None:
            app.dependency_overrides[get_current_user] = original
    assert result.status_code == 401


def test_github_repository_endpoint_returns_normalized_data(github_service):
    github_service.list_repositories = lambda: [{
        "repository_id": "1142228151",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "default_branch": "main",
    }]
    app.dependency_overrides["github_app_service_override"] = github_service
    try:
        result = client.get("/api/v1/integrations/github/repositories?project_id=123456")
    finally:
        app.dependency_overrides.pop("github_app_service_override", None)

    assert result.status_code == 200
    assert result.json() == {"repositories": [{
        "repository_id": "1142228151",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "default_branch": "main",
    }]}
    assert "github-pat-server-only" not in result.text


def test_github_api_failure_is_sanitized(github_service, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: response(
            "https://api.github.com/user/repos",
            {"message": "token=must-not-leak"},
            status_code=401,
        ),
    )

    with pytest.raises(GitHubAppError, match="status=401"):
        github_service.list_repositories()
