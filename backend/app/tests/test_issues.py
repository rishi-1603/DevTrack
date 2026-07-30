"""Tests for the Issues API, including workflow transitions and comments."""
from app.tests.conftest import auth_headers


def _create_project(client, headers, title="Issue Test Project"):
    response = client.post("/projects", json={"title": title}, headers=headers)
    return response.json()["id"]


def test_create_issue(client):
    headers = auth_headers(client, email="issueowner1@example.com")
    project_id = _create_project(client, headers)

    response = client.post(
        "/issues",
        json={"title": "Fix login bug", "priority": "high", "project_id": project_id},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Fix login bug"
    assert body["status"] == "todo"
    assert body["priority"] == "high"


def test_list_issues_with_filters(client):
    headers = auth_headers(client, email="issueowner2@example.com")
    project_id = _create_project(client, headers)

    client.post("/issues", json={"title": "Bug A", "priority": "high", "project_id": project_id}, headers=headers)
    client.post("/issues", json={"title": "Feature B", "priority": "low", "project_id": project_id}, headers=headers)

    response = client.get("/issues", params={"project_id": project_id, "priority": "high"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Bug A"


def test_issue_status_workflow(client):
    headers = auth_headers(client, email="issueowner3@example.com")
    project_id = _create_project(client, headers)
    create_response = client.post(
        "/issues", json={"title": "Workflow Issue", "project_id": project_id}, headers=headers
    )
    issue_id = create_response.json()["id"]

    # todo -> in_progress
    response = client.patch(f"/issues/{issue_id}/status", json={"status": "in_progress"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"

    # in_progress -> done
    response = client.patch(f"/issues/{issue_id}/status", json={"status": "done"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "done"


def test_issue_status_invalid_transition_rejected(client):
    headers = auth_headers(client, email="issueowner4@example.com")
    project_id = _create_project(client, headers)
    create_response = client.post(
        "/issues", json={"title": "Skip Issue", "project_id": project_id}, headers=headers
    )
    issue_id = create_response.json()["id"]

    # todo -> done directly should be rejected
    response = client.patch(f"/issues/{issue_id}/status", json={"status": "done"}, headers=headers)
    assert response.status_code == 400


def test_assign_issue(client):
    owner_headers = auth_headers(client, email="issueowner5@example.com")
    project_id = _create_project(client, headers=owner_headers)
    create_response = client.post(
        "/issues", json={"title": "Assign Me", "project_id": project_id}, headers=owner_headers
    )
    issue_id = create_response.json()["id"]

    dev_headers = auth_headers(client, email="developer1@example.com")
    dev_profile = client.get("/users/me", headers=dev_headers).json()

    response = client.post(
        f"/issues/{issue_id}/assign", json={"user_id": dev_profile["id"]}, headers=owner_headers
    )
    assert response.status_code == 200
    assert response.json()["assigned_to"] == dev_profile["id"]


def test_delete_issue(client):
    headers = auth_headers(client, email="issueowner6@example.com")
    project_id = _create_project(client, headers)
    create_response = client.post(
        "/issues", json={"title": "Delete Me", "project_id": project_id}, headers=headers
    )
    issue_id = create_response.json()["id"]

    response = client.delete(f"/issues/{issue_id}", headers=headers)
    assert response.status_code == 204

    get_response = client.get(f"/issues/{issue_id}", headers=headers)
    assert get_response.status_code == 404


def test_add_and_list_comments(client):
    headers = auth_headers(client, email="commenter1@example.com")
    project_id = _create_project(client, headers)
    create_response = client.post(
        "/issues", json={"title": "Commented Issue", "project_id": project_id}, headers=headers
    )
    issue_id = create_response.json()["id"]

    response = client.post(
        f"/issues/{issue_id}/comments", json={"comment": "Investigating this now."}, headers=headers
    )
    assert response.status_code == 201

    list_response = client.get(f"/issues/{issue_id}/comments", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["comment"] == "Investigating this now."
