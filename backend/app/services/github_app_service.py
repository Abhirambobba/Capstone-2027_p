from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse

import httpx
import jwt

from app.core.config import get_settings


logger = logging.getLogger(__name__)
GITHUB_API = "https://api.github.com"


class GitHubAppError(RuntimeError):
    pass


class GitHubAppConfigurationError(GitHubAppError):
    pass


class GitHubAppService:
    def __init__(self) -> None:
        self._settings_cache = None

    def _settings(self):
        if self._settings_cache is not None:
            return self._settings_cache
        settings = get_settings()
        required = (
            settings.github_app_id,
            settings.github_client_id,
            settings.github_private_key,
        )
        if not all(required):
            raise GitHubAppConfigurationError("GitHub App is not configured")
        self._settings_cache = settings
        return settings

    def _state_key(self) -> bytes:
        settings = self._settings()
        private_key = (settings.github_private_key or "").replace("\\n", "\n").encode()
        client_id = (settings.github_client_id or "").encode()
        return hashlib.sha256(b"astra-github-install-state\0" + client_id + b"\0" + private_key).digest()

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _app_jwt(self) -> str:
        settings = self._settings()
        now = int(time.time())
        private_key = (settings.github_private_key or "").replace("\\n", "\n")
        return jwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": settings.github_app_id},
            private_key,
            algorithm="RS256",
        )

    @staticmethod
    def _safe_return_to(return_to: str | None) -> str:
        if return_to and re.fullmatch(r"/dashboard/projects(?:/[A-Za-z0-9_-]+)?", return_to):
            return return_to
        return "/dashboard/projects"

    def _sign_state(self, employee_id: str, return_to: str) -> str:
        settings = self._settings()
        payload = json.dumps(
            {
                "employee_id": employee_id,
                "return_to": self._safe_return_to(return_to),
                "expires_at": int(time.time()) + 600,
                "nonce": secrets.token_urlsafe(18),
            },
            separators=(",", ":"),
        ).encode()
        encoded = self._b64encode(payload)
        signature = hmac.new(self._state_key(), encoded.encode(), hashlib.sha256).digest()
        return f"{encoded}.{self._b64encode(signature)}"

    def _verify_state(self, state: str | None) -> dict[str, str]:
        if not state or "." not in state:
            raise ValueError("GitHub installation state is invalid")
        encoded, signature = state.split(".", 1)
        settings = self._settings()
        expected = hmac.new(self._state_key(), encoded.encode(), hashlib.sha256).digest()
        try:
            if not hmac.compare_digest(expected, self._b64decode(signature)):
                raise ValueError
            payload = json.loads(self._b64decode(encoded))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("GitHub installation state is invalid") from error
        if not isinstance(payload, dict) or int(payload.get("expires_at", 0)) < int(time.time()):
            raise ValueError("GitHub installation state has expired")
        employee_id = payload.get("employee_id")
        if not isinstance(employee_id, str) or not employee_id:
            raise ValueError("GitHub installation state is invalid")
        return {
            "employee_id": employee_id,
            "return_to": self._safe_return_to(payload.get("return_to")),
        }

    def start_installation(
        self, employee_id: str, return_to: str = "/dashboard/projects"
    ) -> dict[str, str]:
        settings = self._settings()
        app_metadata = self._app_get("/app")
        if str(app_metadata.get("id")) != str(settings.github_app_id):
            raise GitHubAppConfigurationError("Configured GitHub App ID does not match GitHub")
        if app_metadata.get("client_id") != settings.github_client_id:
            raise GitHubAppConfigurationError("Configured GitHub client ID does not match GitHub App")
        slug = app_metadata.get("slug")
        if not isinstance(slug, str) or not slug:
            raise GitHubAppError("GitHub App slug was not available")
        state = self._sign_state(employee_id, return_to)
        url = f"https://github.com/apps/{quote(slug, safe='')}/installations/new"
        return {"install_url": f"{url}?{urlencode({'state': state})}"}

    def callback_redirect(
        self,
        *,
        connected: bool,
        error: str | None = None,
        return_to: str = "/dashboard/projects",
        installation_id: str | None = None,
    ) -> str:
        settings = get_settings()
        destination = (settings.github_frontend_url or "http://localhost:3000").rstrip("/")
        destination += self._safe_return_to(return_to)
        separator = "&" if "?" in destination else "?"
        params = urlencode({"github_status": "connected" if connected else "error"})
        if installation_id:
            params += "&" + urlencode({"github_installation_id": installation_id})
        if error:
            params += "&" + urlencode({"github_error": error})
        return f"{destination}{separator}{params}"

    def _request(
        self,
        method: str,
        url: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        app_auth: bool = False,
    ) -> httpx.Response:
        response = httpx.request(
            method,
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            timeout=20,
        )
        if response.status_code >= 400:
            auth_type = "app" if app_auth else "installation"
            logger.warning(
                "GitHub API request failed: auth_type=%s status_code=%s",
                auth_type,
                response.status_code,
            )
            raise GitHubAppError(f"GitHub API request failed (status={response.status_code})")
        return response

    def _app_get(self, path: str) -> dict[str, Any]:
        response = self._request("GET", f"{GITHUB_API}{path}", self._app_jwt(), app_auth=True)
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubAppError("GitHub returned an invalid response") from error
        if not isinstance(payload, dict):
            raise GitHubAppError("GitHub returned an invalid response")
        return payload

    def complete_installation(
        self,
        installation_id: str | None,
        state: str | None,
    ) -> dict[str, str]:
        state_payload = self._verify_state(state)
        if not installation_id or not installation_id.isdigit():
            raise ValueError("GitHub installation ID is invalid")
        installation = self._app_get(f"/app/installations/{installation_id}")
        if str(installation.get("id")) != installation_id:
            raise ValueError("GitHub installation could not be verified")
        account = installation.get("account") or {}
        account_login = account.get("login")
        if not account_login:
            raise GitHubAppError("GitHub installation account was not available")
        logger.info(
            "GitHub App installation verified: installation_id=%s account_login=%s",
            installation_id,
            account_login,
        )
        return {
            "return_to": state_payload["return_to"],
            "installation_id": installation_id,
        }

    def _installation_token(self, installation_id: str) -> str:
        app_jwt = self._app_jwt()
        response = httpx.post(
            f"{GITHUB_API}/app/installations/{quote(installation_id, safe='')}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20,
        )
        if response.status_code >= 400:
            logger.warning("GitHub installation token request failed: status_code=%s", response.status_code)
            raise GitHubAppError(f"GitHub installation token request failed (status={response.status_code})")
        try:
            token = response.json().get("token")
        except (ValueError, AttributeError) as error:
            raise GitHubAppError("GitHub installation token response was invalid") from error
        if not isinstance(token, str) or not token:
            raise GitHubAppError("GitHub did not return an installation token")
        return token

    @staticmethod
    def _next_page(response: httpx.Response) -> str | None:
        link = getattr(response, "links", {}).get("next", {})
        url = link.get("url") if isinstance(link, dict) else None
        parsed = urlparse(url) if url else None
        if not parsed or parsed.scheme != "https" or parsed.hostname != "api.github.com":
            return None
        return url

    def _list_pages(self, url: str, token: str) -> list[Any]:
        items: list[Any] = []
        next_url: str | None = url
        while next_url:
            response = self._request("GET", next_url, token)
            try:
                payload = response.json()
            except ValueError as error:
                raise GitHubAppError("GitHub returned an invalid response") from error
            if isinstance(payload, list):
                items.extend(payload)
            elif isinstance(payload, dict) and isinstance(payload.get("repositories"), list):
                items.extend(payload["repositories"])
            else:
                raise GitHubAppError("GitHub returned an invalid list response")
            next_url = self._next_page(response)
        return items

    def _installation_repositories(self, installation_id: str) -> list[Any]:
        token = self._installation_token(installation_id)
        return self._list_pages(
            f"{GITHUB_API}/installation/repositories?per_page=100",
            token,
        )

    def list_repositories_for_installation(
        self, installation_id: str
    ) -> list[dict[str, Any]]:
        if not installation_id.isdigit():
            raise ValueError("GitHub installation ID is invalid")
        installation = self._app_get(f"/app/installations/{installation_id}")
        if str(installation.get("id")) != installation_id:
            raise ValueError("GitHub installation could not be verified")
        repositories = []
        for item in self._installation_repositories(installation_id):
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            owner = item.get("owner") or {}
            owner_login = owner.get("login") or ""
            repository_name = item.get("name") or ""
            repositories.append(
                {
                    "repository_id": str(item["id"]),
                    "organization": owner_login,
                    "repository": repository_name,
                    "default_branch": item.get("default_branch") or "main",
                    "installation_id": installation_id,
                    "full_name": item.get("full_name") or f"{owner_login}/{repository_name}",
                }
            )
        return repositories

    def resolve_repository_for_installation(
        self, installation_id: str, repository_id: str
    ) -> dict[str, Any]:
        match = next(
            (
                repository for repository in self.list_repositories_for_installation(installation_id)
                if repository["repository_id"] == str(repository_id)
            ),
            None,
        )
        if match is None:
            raise ValueError("Selected GitHub repository is not available to this installation")
        return match

    def verify_project_repository(
        self, github: dict[str, Any]
    ) -> tuple[str, str, str]:
        installation_id = str(github.get("installation_id") or "")
        repository_id = str(github.get("repository_id") or "")
        organization = str(github.get("organization") or "")
        repository = str(github.get("repository") or "")
        if not installation_id or not installation_id.isdigit():
            raise ValueError("GitHub installation ID is invalid")
        if not repository_id or not organization or not repository:
            raise ValueError("Astra project GitHub mapping is incomplete")
        installation = self._app_get(f"/app/installations/{installation_id}")
        if str(installation.get("id")) != installation_id:
            raise ValueError("GitHub installation could not be verified")
        return installation_id, organization, repository

    def _repository_api(
        self,
        employee_id: str,
        github: dict[str, Any],
        employee_repository: Any,
        path: str,
    ) -> list[Any]:
        installation_id, organization, repository = self.verify_project_repository(
            github
        )
        token = self._installation_token(installation_id)
        endpoint, _, query = path.partition("?")
        params = {"per_page": "100", **dict(parse_qsl(query, keep_blank_values=True))}
        return self._list_pages(
            f"{GITHUB_API}/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/{endpoint}?{urlencode(params)}",
            token,
        )

    def _employee_for_github_user(self, user_id: Any, employee_repository: Any) -> str | None:
        if user_id is None:
            return None
        employee = employee_repository.find_by_github_user_id(str(user_id))
        return str(employee["employee_id"]) if employee else None

    def pull_requests(
        self, employee_id: str, github: dict[str, Any], employee_repository: Any
    ) -> list[dict[str, Any]]:
        raw_pulls = self._repository_api(
            employee_id, github, employee_repository, "pulls?state=all"
        )
        installation_id, organization, repository = self.verify_project_repository(
            github
        )
        token = self._installation_token(installation_id)
        normalized = []
        for pull in raw_pulls:
            if not isinstance(pull, dict):
                continue
            author = pull.get("user") or {}
            head = pull.get("head") or {}
            base = pull.get("base") or {}
            reviews = self._list_pages(
                f"{GITHUB_API}/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/pulls/{pull.get('number')}/reviews?per_page=100",
                token,
            )
            normalized_reviews = []
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                reviewer = review.get("user") or {}
                reviewer_id = str(reviewer["id"]) if reviewer.get("id") is not None else None
                normalized_reviews.append({
                    "id": str(review.get("id") or ""),
                    "login": reviewer.get("login"),
                    "github_user_id": reviewer_id,
                    "employee_id": self._employee_for_github_user(reviewer.get("id"), employee_repository),
                    "state": review.get("state"),
                    "submitted_at": review.get("submitted_at"),
                })
            normalized.append({
                "id": str(pull.get("id") or ""),
                "number": pull.get("number"),
                "title": pull.get("title") or "",
                "state": pull.get("state") or "unknown",
                "author": author.get("login"),
                "author_github_user_id": str(author["id"]) if author.get("id") is not None else None,
                "author_employee_id": self._employee_for_github_user(author.get("id"), employee_repository),
                "created_at": pull.get("created_at"),
                "updated_at": pull.get("updated_at"),
                "merged_at": pull.get("merged_at"),
                "reviewers": [
                    {
                        "login": reviewer.get("login"),
                        "github_user_id": str(reviewer["id"]) if reviewer.get("id") is not None else None,
                        "employee_id": self._employee_for_github_user(reviewer.get("id"), employee_repository),
                    }
                    for reviewer in (pull.get("requested_reviewers") or [])
                    if isinstance(reviewer, dict)
                ] + normalized_reviews,
                "source_branch": head.get("ref"),
                "target_branch": base.get("ref"),
                "url": pull.get("html_url"),
            })
        return normalized

    def commits(
        self, employee_id: str, github: dict[str, Any], employee_repository: Any
    ) -> list[dict[str, Any]]:
        normalized = []
        for commit in self._repository_api(employee_id, github, employee_repository, "commits"):
            if not isinstance(commit, dict):
                continue
            commit_data = commit.get("commit") or {}
            author = commit.get("author") or {}
            commit_author = commit_data.get("author") or {}
            normalized.append({
                "sha": commit.get("sha"),
                "message": commit_data.get("message") or "",
                "author": author.get("login") or commit_author.get("name"),
                "author_github_user_id": str(author["id"]) if author.get("id") is not None else None,
                "author_employee_id": self._employee_for_github_user(author.get("id"), employee_repository),
                "timestamp": commit_author.get("date"),
                "url": commit.get("html_url"),
            })
        return normalized

    def issues(
        self, employee_id: str, github: dict[str, Any], employee_repository: Any
    ) -> list[dict[str, Any]]:
        normalized = []
        for issue in self._repository_api(employee_id, github, employee_repository, "issues?state=all"):
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            author = issue.get("user") or {}
            assignees = issue.get("assignees") or []
            assignee = assignees[0] if assignees and isinstance(assignees[0], dict) else {}
            normalized.append({
                "id": str(issue.get("id") or ""),
                "number": issue.get("number"),
                "title": issue.get("title") or "",
                "state": issue.get("state") or "unknown",
                "author": author.get("login"),
                "author_github_user_id": str(author["id"]) if author.get("id") is not None else None,
                "author_employee_id": self._employee_for_github_user(author.get("id"), employee_repository),
                "assignee": assignee.get("login"),
                "assignee_github_user_id": str(assignee["id"]) if assignee.get("id") is not None else None,
                "assignee_employee_id": self._employee_for_github_user(assignee.get("id"), employee_repository),
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "url": issue.get("html_url"),
            })
        return normalized


_DEFAULT_GITHUB_APP_SERVICE = GitHubAppService()


def get_github_app_service() -> GitHubAppService:
    return _DEFAULT_GITHUB_APP_SERVICE