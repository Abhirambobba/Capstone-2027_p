from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.github_integrations import get_project_repository
from app.core.auth import get_current_user
from app.main import app
from app.services.github_app_service import GitHubAppError, GitHubAppService


client = TestClient(app)


class FakeEmployeeRepository:
    def __init__(self):
        self.items = {
            "EMP_001": {
                "employee_id": "EMP_001",
                "github": {
                    "user_id": "101",
                    "login": "astra-author",
                    "installations": [
                        {"installation_id": "98765", "account_login": "astra-org", "account_type": "Organization"}
                    ],
                },
            }
        }

    def get(self, employee_id):
        return self.items.get(employee_id)

    def update(self, employee_id, fields):
        self.items[employee_id].update(fields)
        return self.items[employee_id]

    def find_by_github_user_id(self, user_id):
        return next(
            (
                item for item in self.items.values()
                if str((item.get("github") or {}).get("user_id")) == str(user_id)
            ),
            None,
        )


@pytest.fixture
def github_service(monkeypatch):
    monkeypatch.setenv("DYNAMODB_EMPLOYEES_TABLE", "AstraEmployees")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.client-id")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", "private-key-is-server-only")
    monkeypatch.setenv("GITHUB_FRONTEND_URL", "http://localhost:3000")
    service = GitHubAppService()
    return service


def response(url: str, payload, status_code: int = 200):
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", url),
    )


def project_github():
    return {
        "provider": "github",
        "organization": "astra-org",
        "repository": "astra-app",
        "repository_id": "123456",
        "default_branch": "main",
        "installation_id": "98765",
    }


def test_github_installation_url_has_signed_employee_state(github_service):
    github_service._app_get = lambda path: {
        "id": 12345,
        "client_id": "Iv1.client-id",
        "slug": "astra-evidence",
    }
    result = github_service.start_installation("EMP_001", "/dashboard/projects/PROJ_1")
    parsed = urlparse(result["install_url"])
    state = parse_qs(parsed.query)["state"][0]

    assert parsed.netloc == "github.com"
    assert parsed.path == "/apps/astra-evidence/installations/new"
    assert github_service._verify_state(state) == {
        "employee_id": "EMP_001",
        "return_to": "/dashboard/projects/PROJ_1",
    }
    assert "private-key-is-server-only" not in result["install_url"]


def test_github_install_callback_verifies_installation_without_employee_lookup(github_service, monkeypatch):
    github_service._app_jwt = lambda: "app-jwt-only-on-server"
    state = github_service._sign_state("EMP_001", "/dashboard/projects/PROJ_1")

    def fake_request(method, url, headers=None, **kwargs):
        assert method == "GET"
        assert url == "https://api.github.com/app/installations/98765"
        assert headers["Authorization"] == "Bearer app-jwt-only-on-server"
        return response(url, {"id": 98765, "account": {"login": "astra-org", "type": "Organization"}})

    monkeypatch.setattr(httpx, "request", fake_request)
    result = github_service.complete_installation("98765", state)

    assert result == {
        "return_to": "/dashboard/projects/PROJ_1",
        "installation_id": "98765",
    }


def test_github_repository_discovery_uses_installation_token_server_side(github_service, monkeypatch):
    github_service._installation_token = lambda installation_id: "installation-token-secret"
    github_service._app_get = lambda path: {"id": "98765"}

    def fake_request(method, url, headers=None, **kwargs):
        assert headers["Authorization"] == "Bearer installation-token-secret"
        assert "/installation/repositories" in url
        return response(url, {"repositories": [{
            "id": 123456,
            "name": "astra-app",
            "full_name": "astra-org/astra-app",
            "default_branch": "main",
            "private": True,
            "html_url": "https://github.com/astra-org/astra-app",
            "owner": {"login": "astra-org"},
        }]})

    monkeypatch.setattr(httpx, "request", fake_request)
    repositories = github_service.list_repositories_for_installation("98765")

    assert repositories == [{
        "repository_id": "123456",
        "organization": "astra-org",
        "repository": "astra-app",
        "full_name": "astra-org/astra-app",
        "default_branch": "main",
        "installation_id": "98765",
    }]
    assert "installation-token-secret" not in str(repositories)


def test_github_reads_normalize_pr_commits_issues_and_attribute_real_users(github_service, monkeypatch):
    employee_repository = FakeEmployeeRepository()
    employee_repository.items["EMP_002"] = {
        "employee_id": "EMP_002",
        "github": {"user_id": "202", "login": "reviewer", "installations": []},
    }
    github_service._installation_token = lambda installation_id: "installation-token-secret"
    github_service._app_get = lambda path: {"id": "98765"}

    def fake_request(method, url, headers=None, **kwargs):
        assert headers["Authorization"] == "Bearer installation-token-secret"
        if "/pulls/7/reviews" in url:
            return response(url, [{
                "id": 77,
                "user": {"id": 202, "login": "reviewer"},
                "state": "APPROVED",
                "submitted_at": "2026-10-01T10:00:00Z",
            }])
        if "/pulls?" in url:
            return response(url, [{
                "id": 700,
                "number": 7,
                "title": "Ship evidence pipeline",
                "state": "closed",
                "merged_at": "2026-10-01T11:00:00Z",
                "created_at": "2026-09-30T10:00:00Z",
                "updated_at": "2026-10-01T11:00:00Z",
                "html_url": "https://github.com/astra-org/astra-app/pull/7",
                "user": {"id": 101, "login": "astra-author"},
                "head": {"ref": "feature/evidence"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }])
        if "/commits?" in url:
            return response(url, [{
                "sha": "abc123",
                "html_url": "https://github.com/astra-org/astra-app/commit/abc123",
                "author": {"id": 101, "login": "astra-author"},
                "commit": {"message": "Add evidence", "author": {"name": "Author", "date": "2026-10-01T12:00:00Z"}},
            }])
        if "/issues?" in url:
            return response(url, [
                {
                    "id": 900,
                    "number": 9,
                    "title": "Review evidence ingestion",
                    "state": "open",
                    "created_at": "2026-10-01T08:00:00Z",
                    "updated_at": "2026-10-01T09:00:00Z",
                    "html_url": "https://github.com/astra-org/astra-app/issues/9",
                    "user": {"id": 101, "login": "astra-author"},
                    "assignees": [{"id": 202, "login": "reviewer"}],
                },
                {"id": 701, "number": 7, "pull_request": {"url": "ignored"}},
            ])
        raise AssertionError(f"Unexpected GitHub URL: {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    github = project_github()
    pulls = github_service.pull_requests("EMP_001", github, employee_repository)
    commits = github_service.commits("EMP_001", github, employee_repository)
    issues = github_service.issues("EMP_001", github, employee_repository)

    assert pulls[0]["id"] == "700"
    assert pulls[0]["author_employee_id"] == "EMP_001"
    assert pulls[0]["reviewers"][0]["employee_id"] == "EMP_002"
    assert pulls[0]["source_branch"] == "feature/evidence"
    assert commits[0]["sha"] == "abc123"
    assert commits[0]["author_employee_id"] == "EMP_001"
    assert issues[0]["id"] == "900"
    assert issues[0]["assignee_employee_id"] == "EMP_002"
    assert len(issues) == 1
    assert "installation-token-secret" not in str((pulls, commits, issues))


def test_github_api_failure_returns_sanitized_status(github_service, monkeypatch):
    employee_repository = FakeEmployeeRepository()
    github_service._installation_token = lambda installation_id: "installation-token-secret"
    github_service._app_get = lambda path: {"id": "98765"}
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: response(
            "https://api.github.com/installation/repositories?per_page=100",
            {"message": "token=must-not-leak"},
            status_code=401,
        ),
    )

    with pytest.raises(GitHubAppError, match="status=401") as raised:
        github_service.list_repositories_for_installation("98765")

    assert "must-not-leak" not in str(raised.value)
    assert "installation-token-secret" not in str(raised.value)


def test_github_repository_endpoint_requires_authentication(monkeypatch):
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        result = client.get("/api/v1/integrations/github/repositories?installation_id=98765&project_id=PROJ_001")
    finally:
        if original is not None:
            app.dependency_overrides[get_current_user] = original
    assert result.status_code == 401


def test_github_repository_endpoint_never_exposes_credentials(monkeypatch):
    class FakeGitHubService:
        def list_repositories_for_installation(self, installation_id):
            assert installation_id == "98765"
            return [{
                "repository_id": "123456",
                "organization": "astra-org",
                "repository": "astra-app",
                "full_name": "astra-org/astra-app",
                "default_branch": "main",
                "installation_id": "98765",
                "private": True,
                "html_url": "https://github.com/astra-org/astra-app",
            }]

    app.dependency_overrides["github_app_service_override"] = FakeGitHubService()
    try:
        result = client.get("/api/v1/integrations/github/repositories?installation_id=98765&project_id=PROJ_001")
    finally:
        app.dependency_overrides.pop("github_app_service_override", None)

    assert result.status_code == 200
    assert "private-key" not in result.text
    assert "installation-token" not in result.text
    assert "access_token" not in result.text
    assert "secret" not in result.text


def test_github_repository_endpoint_reports_missing_installation():
    class FakeGitHubService:
        def list_repositories_for_installation(self, installation_id):
            raise ValueError("GitHub installation could not be verified")

    app.dependency_overrides["github_app_service_override"] = FakeGitHubService()
    try:
        result = client.get("/api/v1/integrations/github/repositories?installation_id=167959604&project_id=PROJ_001")
    finally:
        app.dependency_overrides.pop("github_app_service_override", None)

    assert result.status_code == 404
    assert result.json()["detail"] == "GitHub installation could not be verified"


def test_github_evidence_endpoint_enforces_project_owner():
    class FakeProjectRepository:
        def get(self, project_id):
            return {
                "project_id": project_id,
                "owner_employee_id": "SOME_OTHER_EMPLOYEE",
                "github": project_github(),
            }

    app.dependency_overrides[get_project_repository] = lambda: FakeProjectRepository()
    try:
        result = client.get("/api/v1/integrations/github/projects/PROJ_001/pulls")
    finally:
        app.dependency_overrides.pop(get_project_repository, None)

    assert result.status_code == 403
    assert result.json()["detail"] == "Project access denied"


def test_debug_repository_endpoint_uses_requested_installation_and_returns_safe_fields(
    github_service, monkeypatch
):
    github_service._installation_token = lambda installation_id: "debug-installation-token"
    github_service._app_get = lambda path: {"id": "167959604"}

    def fake_request(method, url, headers=None, **kwargs):
        assert method == "GET"
        assert url == "https://api.github.com/installation/repositories?per_page=100"
        assert headers["Authorization"] == "Bearer debug-installation-token"
        return response(url, {"repositories": [{
            "id": 246810,
            "name": "MCP-Capstone",
            "full_name": "souriesh/MCP-Capstone",
            "owner": {"login": "souriesh"},
            "default_branch": "main",
            "private": True,
        }]})

    monkeypatch.setattr(httpx, "request", fake_request)
    app.dependency_overrides["github_app_service_override"] = github_service
    try:
        result = client.get(
            "/api/v1/integrations/github/debug/installations/167959604/repositories"
        )
    finally:
        app.dependency_overrides.pop("github_app_service_override", None)

    assert result.status_code == 200
    assert result.json() == {"repositories": [{
        "repository_id": "246810",
        "organization": "souriesh",
        "repository": "MCP-Capstone",
        "full_name": "souriesh/MCP-Capstone",
        "default_branch": "main",
        "installation_id": "167959604",
    }]}
    assert "debug-installation-token" not in result.text


def test_debug_repository_endpoint_does_not_require_employee_installation(github_service, monkeypatch):
    github_service._app_get = lambda path: {"id": "167959604"}
    github_service._installation_token = lambda installation_id: "temporary-token"
    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, **kwargs: response(url, {"repositories": []}),
    )
    app.dependency_overrides["github_app_service_override"] = github_service
    try:
        result = client.get(
            "/api/v1/integrations/github/debug/installations/167959604/repositories"
        )
    finally:
        app.dependency_overrides.pop("github_app_service_override", None)

    assert result.status_code == 200
    assert result.json() == {"repositories": []}


def test_debug_repository_endpoint_requires_authentication():
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        result = client.get(
            "/api/v1/integrations/github/debug/installations/167959604/repositories"
        )
    finally:
        if original is not None:
            app.dependency_overrides[get_current_user] = original

    assert result.status_code == 401