"""Tests for registration, login, and JWT validation."""
from app.tests.conftest import login_user, register_user


def test_register_success(client):
    response = register_user(client, email="newuser@example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "newuser@example.com"
    assert body["role"] == "developer"
    assert "password" not in body


def test_register_duplicate_email_fails(client):
    register_user(client, email="dupe@example.com")
    response = register_user(client, email="dupe@example.com")
    assert response.status_code == 409


def test_login_success(client):
    register_user(client, email="loginok@example.com", password="strongpass1")
    response = login_user(client, email="loginok@example.com", password="strongpass1")
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


def test_login_wrong_password_fails(client):
    register_user(client, email="wrongpass@example.com", password="correctpass1")
    response = login_user(client, email="wrongpass@example.com", password="incorrectpass")
    assert response.status_code == 401


def test_protected_route_rejects_invalid_token(client):
    response = client.get("/users/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_protected_route_rejects_missing_token(client):
    response = client.get("/users/me")
    assert response.status_code == 401


def test_protected_route_accepts_valid_token(client):
    register_user(client, email="validtoken@example.com", password="password123")
    login_response = login_user(client, email="validtoken@example.com", password="password123")
    token = login_response.json()["access_token"]

    response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "validtoken@example.com"


def test_refresh_token_flow(client):
    register_user(client, email="refresh@example.com", password="password123")
    login_response = login_user(client, email="refresh@example.com", password="password123")
    refresh_token = login_response.json()["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_refresh_rejects_access_token(client):
    register_user(client, email="badrefresh@example.com", password="password123")
    login_response = login_user(client, email="badrefresh@example.com", password="password123")
    access_token = login_response.json()["access_token"]

    response = client.post("/auth/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


def test_change_password(client):
    register_user(client, email="changepw@example.com", password="oldpassword1")
    login_response = login_user(client, email="changepw@example.com", password="oldpassword1")
    token = login_response.json()["access_token"]

    response = client.post(
        "/auth/change-password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    # Old password should no longer work
    old_login = login_user(client, email="changepw@example.com", password="oldpassword1")
    assert old_login.status_code == 401

    # New password should work
    new_login = login_user(client, email="changepw@example.com", password="newpassword1")
    assert new_login.status_code == 200
