from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from app.core.auth import AuthenticatedUser, get_current_user
from app.main import app
from app.models.employee import Employee


def make_test_user(access_role: str = "MANAGER") -> AuthenticatedUser:
    timestamp = datetime.now(timezone.utc)
    employee = Employee(
        employee_id="AUTH_TEST_EMPLOYEE",
        name="Test User",
        email="test@example.com",
        role="Test role",
        department="Testing",
        access_role=access_role,
        auth={"cognito_user_id": "auth-test-sub"},
        employment_status="ACTIVE",
        created_at=timestamp,
        updated_at=timestamp,
    )
    return AuthenticatedUser(employee=employee, cognito_user_id="auth-test-sub")


@pytest.fixture
def auth_user() -> AuthenticatedUser:
    return make_test_user()


@pytest.fixture(autouse=True)
def install_test_auth(app_instance: FastAPI, auth_user: AuthenticatedUser):
    previous = app_instance.dependency_overrides.get(get_current_user)
    app_instance.dependency_overrides[get_current_user] = lambda: auth_user
    yield
    if previous is None:
        app_instance.dependency_overrides.pop(get_current_user, None)
    else:
        app_instance.dependency_overrides[get_current_user] = previous


@pytest.fixture
def app_instance() -> FastAPI:
    return app