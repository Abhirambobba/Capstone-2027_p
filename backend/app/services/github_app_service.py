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
        if not settings.github_personal_access_token:
            raise GitHubAppConfigurationError("GitHub personal access token is not configured")
        self._settings_cache = settings
        return settings

    def _state_key(self) -> bytes:
        settings = self._settings()
        token = (settings.github_personal_access_token or "").encode()
        return hashlib.sha256(b"astra-github-pat-state\0" + token).digest()

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

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

    def start_connection(
        self, employee_id: str, return_to: str = "/dashboard/projects"
    ) -> dict[str, str]:
        self._github_get("/user")
        return {"connected": "true", "return_to": self._safe_return_to(return_to)}

    def callback_redirect(
        self,
        *,
        connected: bool,
        error: str | None = None,
        return_to: str = "/dashboard/projects",
    ) -> str:
        settings = get_settings()
        destination = (settings.github_frontend_url or "http://localhost:3000").rstrip("/")
        destination += self._safe_return_to(return_to)
        separator = "&" if "?" in destination else "?"
        params = urlencode({"github_status": "connected" if connected else "error"})
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

    def _github_get(self, path: str) -> dict[str, Any]:
        response = self._request("GET", f"{GITHUB_API}{path}", self._settings().github_personal_access_token or "")
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubAppError("GitHub returned an invalid response") from error
        if not isinstance(payload, dict):
            raise GitHubAppError("GitHub returned an invalid response")
        return payload

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

    def _user_repositories(self) -> list[Any]:
        return self._list_pages(
            f"{GITHUB_API}/user/repos?per_page=100&affiliation=owner,collaborator,organization_member",
            self._settings().github_personal_access_token or "",
        )

    def list_repositories(self) -> list[dict[str, Any]]:
        repositories = []
        for item in self._user_repositories():
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
                    "full_name": item.get("full_name") or f"{owner_login}/{repository_name}",
                }
            )
        return repositories

    def resolve_repository(self, repository_id: str) -> dict[str, Any]:
        match = next(
            (
                repository for repository in self.list_repositories()
                if repository["repository_id"] == str(repository_id)
            ),
            None,
        )
        if match is None:
            raise ValueError("Selected GitHub repository is not available to the configured GitHub account")
        return match

    def verify_project_repository(
        self, github: dict[str, Any]
    ) -> tuple[str, str]:
        repository_id = str(github.get("repository_id") or "")
        organization = str(github.get("organization") or "")
        repository = str(github.get("repository") or "")
        if not repository_id or not organization or not repository:
            raise ValueError("Astra project GitHub mapping is incomplete")
        return organization, repository

    def _repository_api(
        self,
        employee_id: str,
        github: dict[str, Any],
        employee_repository: Any,
        path: str,
    ) -> list[Any]:
        organization, repository = self.verify_project_repository(
            github
        )
        token = self._settings().github_personal_access_token or ""
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
        organization, repository = self.verify_project_repository(
            github
        )
        token = self._settings().github_personal_access_token or ""
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