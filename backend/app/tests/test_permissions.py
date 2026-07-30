"""Tests for RBAC / ownership permission checks."""
from app.database.models import User, UserRole
from app.tests.conftest import TestingSessionLocal, auth_headers


def _promote_to_admin(email: str) -> None:
    """Test helper: directly promote a user to admin in the DB."""
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.role = UserRole.ADMIN
        db.add(user)
        db.commit()
    finally:
        db.close()


def test_non_owner_cannot_update_project(client):
    owner_headers = auth_headers(client, email="realowner@example.com")
    create_response = client.post("/projects", json={"title": "Owner's Project"}, headers=owner_headers)
    project_id = create_response.json()["id"]

    other_headers = auth_headers(client, email="intruder@example.com")
    response = client.put(
        f"/projects/{project_id}", json={"title": "Hijacked"}, headers=other_headers
    )
    assert response.status_code == 403


def test_non_owner_cannot_delete_project(client):
    owner_headers = auth_headers(client, email="realowner2@example.com")
    create_response = client.post("/projects", json={"title": "Owner's Project 2"}, headers=owner_headers)
    project_id = create_response.json()["id"]

    other_headers = auth_headers(client, email="intruder2@example.com")
    response = client.delete(f"/projects/{project_id}", headers=other_headers)
    assert response.status_code == 403


def test_non_admin_cannot_delete_user(client):
    headers = auth_headers(client, email="regularuser@example.com")
    profile = client.get("/users/me", headers=headers).json()

    response = client.delete(f"/users/{profile['id']}", headers=headers)
    assert response.status_code == 403


def test_admin_can_delete_user(client):
    headers = auth_headers(client, email="targetuser@example.com")
    target_profile = client.get("/users/me", headers=headers).json()

    admin_headers = auth_headers(client, email="adminuser@example.com")
    _promote_to_admin("adminuser@example.com")

    # Re-login so downstream logic that reads role from token subject still resolves the fresh user object.
    response = client.delete(f"/users/{target_profile['id']}", headers=admin_headers)
    assert response.status_code == 204


def test_non_author_cannot_delete_comment(client):
    owner_headers = auth_headers(client, email="commentowner@example.com")
    project_response = client.post("/projects", json={"title": "Comment Project"}, headers=owner_headers)
    project_id = project_response.json()["id"]
    issue_response = client.post(
        "/issues", json={"title": "Issue for comments", "project_id": project_id}, headers=owner_headers
    )
    issue_id = issue_response.json()["id"]
    comment_response = client.post(
        f"/issues/{issue_id}/comments", json={"comment": "First!"}, headers=owner_headers
    )
    comment_id = comment_response.json()["id"]

    other_headers = auth_headers(client, email="notauthor@example.com")
    response = client.delete(f"/comments/{comment_id}", headers=other_headers)
    assert response.status_code == 403


def test_author_can_delete_own_comment(client):
    headers = auth_headers(client, email="commentauthor@example.com")
    project_response = client.post("/projects", json={"title": "Author Project"}, headers=headers)
    project_id = project_response.json()["id"]
    issue_response = client.post(
        "/issues", json={"title": "Issue for own comment", "project_id": project_id}, headers=headers
    )
    issue_id = issue_response.json()["id"]
    comment_response = client.post(
        f"/issues/{issue_id}/comments", json={"comment": "My own comment"}, headers=headers
    )
    comment_id = comment_response.json()["id"]

    response = client.delete(f"/comments/{comment_id}", headers=headers)
    assert response.status_code == 204
