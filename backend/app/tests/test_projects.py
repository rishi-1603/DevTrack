"""Tests for the Projects API."""
from app.tests.conftest import auth_headers


def test_create_project(client):
    headers = auth_headers(client, email="owner1@example.com")
    response = client.post(
        "/projects",
        json={"title": "DevTrack Core", "description": "Backend for DevTrack"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "DevTrack Core"
    assert body["owner"]["email"] == "owner1@example.com"


def test_list_projects_with_search(client):
    headers = auth_headers(client, email="owner2@example.com")
    client.post("/projects", json={"title": "Alpha Rocket"}, headers=headers)
    client.post("/projects", json={"title": "Beta Launcher"}, headers=headers)

    response = client.get("/projects", params={"search": "Rocket"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Alpha Rocket"


def test_get_project_by_id(client):
    headers = auth_headers(client, email="owner3@example.com")
    create_response = client.post("/projects", json={"title": "Gamma"}, headers=headers)
    project_id = create_response.json()["id"]

    response = client.get(f"/projects/{project_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == "Gamma"


def test_get_project_not_found(client):
    headers = auth_headers(client, email="owner4@example.com")
    response = client.get("/projects/9999", headers=headers)
    assert response.status_code == 404


def test_update_project_by_owner(client):
    headers = auth_headers(client, email="owner5@example.com")
    create_response = client.post("/projects", json={"title": "Old Title"}, headers=headers)
    project_id = create_response.json()["id"]

    response = client.put(f"/projects/{project_id}", json={"title": "New Title"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == "New Title"


def test_delete_project_by_owner(client):
    headers = auth_headers(client, email="owner6@example.com")
    create_response = client.post("/projects", json={"title": "To Delete"}, headers=headers)
    project_id = create_response.json()["id"]

    response = client.delete(f"/projects/{project_id}", headers=headers)
    assert response.status_code == 204

    get_response = client.get(f"/projects/{project_id}", headers=headers)
    assert get_response.status_code == 404
