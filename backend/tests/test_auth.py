from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import auth
from app.core.auth import get_current_user, require_manager
from app.main import app
from conftest import make_test_user


@pytest.fixture
def auth_user():
    return make_test_user("EMPLOYEE")


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJwksClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return FakeSigningKey(self.key)


@pytest.fixture
def cognito_keys(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setenv("COGNITO_REGION", "ap-south-2")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "ap-south-2_testpool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")
    monkeypatch.setenv("DYNAMODB_EMPLOYEES_TABLE", "employees")
    monkeypatch.setattr(auth, "_jwks_client", lambda _region, _pool: FakeJwksClient(public_key))
    return private_key


def token_for(private_key, **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://cognito-idp.ap-south-2.amazonaws.com/ap-south-2_testpool",
        "sub": "cognito-sub",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "token_use": "access",
        "client_id": "test-client",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})


def test_verify_cognito_access_token(cognito_keys):
    claims = auth.verify_cognito_token(token_for(cognito_keys))
    assert claims["sub"] == "cognito-sub"


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "another-client"},
        {"iss": "https://wrong-issuer.example"},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
    ],
)
def test_reject_invalid_cognito_token(cognito_keys, overrides):
    with pytest.raises(HTTPException) as error:
        auth.verify_cognito_token(token_for(cognito_keys, **overrides))
    assert error.value.status_code == 401


def test_employee_is_not_authorized_as_manager():
    employee = make_test_user("EMPLOYEE")
    with pytest.raises(HTTPException) as error:
        require_manager(employee)
    assert error.value.status_code == 403


def test_manager_is_authorized():
    manager = make_test_user("MANAGER")
    assert require_manager(manager) is manager


def test_management_get_requires_authentication():
    app.dependency_overrides.pop(get_current_user, None)
    response = TestClient(app).get("/api/v1/management/employees")
    assert response.status_code == 401


def test_me_uses_authenticated_employee_not_query_parameter():
    response = TestClient(app).get("/api/v1/me?employee_id=OTHER_EMPLOYEE")
    assert response.status_code == 200
    assert response.json()["employee_id"] == "AUTH_TEST_EMPLOYEE"


def test_employee_cannot_create_management_employee():
    response = TestClient(app).post(
        "/api/v1/management/employees",
        json={
            "employee_id": "NEW_EMPLOYEE",
            "name": "New Employee",
            "email": "new@example.com",
            "role": "Engineer",
            "department": "Engineering",
        },
    )
    assert response.status_code == 403