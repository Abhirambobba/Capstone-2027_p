from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError

from app.core.config import get_settings
from app.core.dynamodb import get_employees_table
from app.models.employee import AccessRole, Employee
from app.repositories.employee_repository import (
    EmployeeIdentityConflictError,
    EmployeeRepository,
)

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    employee: Employee
    cognito_user_id: str

    @property
    def employee_id(self) -> str:
        return self.employee.employee_id

    @property
    def access_role(self) -> AccessRole:
        return self.employee.access_role


@lru_cache(maxsize=4)
def _jwks_client(region: str, user_pool_id: str) -> PyJWKClient:
    jwks_url = (
        f"https://cognito-idp.{region}.amazonaws.com/"
        f"{user_pool_id}/.well-known/jwks.json"
    )
    return PyJWKClient(jwks_url)


def verify_cognito_token(token: str) -> dict:
    settings = get_settings()
    if not settings.cognito_region or not settings.cognito_user_pool_id or not settings.cognito_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito authentication is not configured",
        )

    issuer = (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )
    try:
        signing_key = _jwks_client(
            settings.cognito_region, settings.cognito_user_pool_id
        ).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "iss", "sub", "token_use"],
            },
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito signing keys are unavailable",
        ) from error
    except (jwt.PyJWTError, PyJWKClientError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Cognito token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    token_use = claims.get("token_use")
    if token_use == "access":
        valid_client = claims.get("client_id") == settings.cognito_client_id
    elif token_use == "id":
        valid_client = claims.get("aud") == settings.cognito_client_id
    else:
        valid_client = False
    if not valid_client or not isinstance(claims.get("sub"), str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not valid for this Cognito app client",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = verify_cognito_token(credentials.credentials)
    cognito_user_id = claims["sub"]
    try:
        record = EmployeeRepository(get_employees_table()).find_by_cognito_user_id(
            cognito_user_id
        )
    except EmployeeIdentityConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cognito identity is linked to multiple Astra employees",
        ) from error
    except Exception as error:
        logger.exception("Employee identity lookup failed for Cognito sub=%s", cognito_user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Employee identity lookup is unavailable",
        ) from error
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cognito user is not linked to an Astra employee",
        )
    return AuthenticatedUser(
        employee=Employee.model_validate(record), cognito_user_id=cognito_user_id
    )


def require_authenticated_user(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    return current_user


def require_manager(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    if current_user.access_role != "MANAGER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager access required",
        )
    return current_user