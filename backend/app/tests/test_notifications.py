"""Tests for the notification system: persisted REST notifications + live WebSocket push.

Covers:
  - assigning an issue to someone else creates a persisted notification for them
  - self-assignment does NOT create notification noise
  - unread_only filtering and mark-read / mark-all-read
  - a user cannot mark another user's notification as read (authorization)
  - the WebSocket endpoint rejects a missing/invalid token
  - the WebSocket endpoint delivers the unread backlog on connect
  - a live event pushed *after* connecting is actually received on the socket
    (this is the real proof that "realtime" isn't just marketing -- an
    assignment made over a second HTTP connection while the WebSocket is open
    shows up on the socket without polling)
"""
from app.tests.conftest import auth_headers


def _create_project(client, headers, title="Notif Test Project"):
    response = client.post("/projects", json={"title": title}, headers=headers)
    return response.json()["id"]


def test_assigning_issue_creates_notification_for_assignee(client):
    owner_headers = auth_headers(client, email="notif_owner1@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "Needs assignment", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="notif_dev1@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]

    response = client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)
    assert response.status_code == 200

    notif_response = client.get("/notifications", headers=dev_headers)
    assert notif_response.status_code == 200
    body = notif_response.json()
    assert body["total"] == 1
    assert body["unread_count"] == 1
    assert body["items"][0]["type"] == "issue_assigned"
    assert "assigned you" in body["items"][0]["message"]
    assert body["items"][0]["issue_id"] == issue_id


def test_self_assignment_does_not_create_notification(client):
    headers = auth_headers(client, email="notif_self@example.com")
    project_id = _create_project(client, headers)
    issue_id = client.post(
        "/issues", json={"title": "Self assign", "project_id": project_id}, headers=headers
    ).json()["id"]
    my_id = client.get("/users/me", headers=headers).json()["id"]

    response = client.post(f"/issues/{issue_id}/assign", json={"user_id": my_id}, headers=headers)
    assert response.status_code == 200

    notif_response = client.get("/notifications", headers=headers)
    assert notif_response.json()["total"] == 0


def test_status_change_notifies_assignee_not_the_actor(client):
    owner_headers = auth_headers(client, email="notif_owner2@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "Status flow", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="notif_dev2@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    # Owner (not the assignee) changes status -> assignee should be notified.
    response = client.patch(f"/issues/{issue_id}/status", json={"status": "in_progress"}, headers=owner_headers)
    assert response.status_code == 200

    notif_response = client.get("/notifications", params={"unread_only": True}, headers=dev_headers)
    types = [item["type"] for item in notif_response.json()["items"]]
    assert "issue_status_changed" in types


def test_comment_notifies_assignee(client):
    owner_headers = auth_headers(client, email="notif_owner3@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "Commented flow", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="notif_dev3@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    client.post(f"/issues/{issue_id}/comments", json={"comment": "Looking into it."}, headers=owner_headers)

    notif_response = client.get("/notifications", headers=dev_headers)
    types = [item["type"] for item in notif_response.json()["items"]]
    assert "issue_commented" in types


def test_mark_notification_read(client):
    owner_headers = auth_headers(client, email="notif_owner4@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "Read flow", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="notif_dev4@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    notif_id = client.get("/notifications", headers=dev_headers).json()["items"][0]["id"]

    response = client.post(f"/notifications/{notif_id}/read", headers=dev_headers)
    assert response.status_code == 200
    assert response.json()["is_read"] is True

    unread = client.get("/notifications", params={"unread_only": True}, headers=dev_headers).json()
    assert unread["total"] == 0


def test_cannot_mark_another_users_notification_read(client):
    owner_headers = auth_headers(client, email="notif_owner5@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "Auth flow", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="notif_dev5@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    notif_id = client.get("/notifications", headers=dev_headers).json()["items"][0]["id"]

    # A third, unrelated user tries to mark it read -> must be forbidden.
    other_headers = auth_headers(client, email="notif_intruder@example.com")
    response = client.post(f"/notifications/{notif_id}/read", headers=other_headers)
    assert response.status_code == 403


def test_mark_all_read(client):
    owner_headers = auth_headers(client, email="notif_owner6@example.com")
    project_id = _create_project(client, owner_headers)
    dev_headers = auth_headers(client, email="notif_dev6@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]

    for i in range(3):
        issue_id = client.post(
            "/issues", json={"title": f"Bulk {i}", "project_id": project_id}, headers=owner_headers
        ).json()["id"]
        client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    assert client.get("/notifications", headers=dev_headers).json()["unread_count"] == 3

    response = client.post("/notifications/read-all", headers=dev_headers)
    assert response.status_code == 204
    assert client.get("/notifications", headers=dev_headers).json()["unread_count"] == 0


def test_websocket_rejects_invalid_token(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/notifications?token=not-a-real-token"):
            pass


def test_websocket_delivers_unread_backlog_on_connect(client):
    owner_headers = auth_headers(client, email="ws_owner1@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "WS backlog", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="ws_dev1@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    dev_token = dev_headers["Authorization"].split(" ")[1]

    # Notification created BEFORE the socket connects.
    client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)

    with client.websocket_connect(f"/ws/notifications?token={dev_token}") as ws:
        backlog = ws.receive_json()
        assert backlog["event"] == "unread_backlog"
        assert backlog["unread_count"] == 1
        assert backlog["items"][0]["type"] == "issue_assigned"


def test_websocket_receives_live_push_after_connecting(client):
    """Proves this is genuinely real-time: the event is created via a second,
    independent HTTP call *while the socket is open*, and must arrive on the
    socket without the client polling anything."""
    owner_headers = auth_headers(client, email="ws_owner2@example.com")
    project_id = _create_project(client, owner_headers)
    issue_id = client.post(
        "/issues", json={"title": "WS live push", "project_id": project_id}, headers=owner_headers
    ).json()["id"]

    dev_headers = auth_headers(client, email="ws_dev2@example.com")
    dev_id = client.get("/users/me", headers=dev_headers).json()["id"]
    dev_token = dev_headers["Authorization"].split(" ")[1]

    with client.websocket_connect(f"/ws/notifications?token={dev_token}") as ws:
        backlog = ws.receive_json()
        assert backlog["unread_count"] == 0  # nothing assigned yet

        # Trigger the notification via a normal REST call while connected.
        response = client.post(f"/issues/{issue_id}/assign", json={"user_id": dev_id}, headers=owner_headers)
        assert response.status_code == 200

        pushed = ws.receive_json()
        assert pushed["type"] == "issue_assigned"
        assert pushed["issue_id"] == issue_id
