"""Tests for the WebSocket ticket (Day-7 security remediation, finding S9).

The credential for `/ws/notifications` has to travel in a query parameter,
because browsers cannot set an `Authorization` header on a WebSocket handshake.
Query parameters are logged by default in most things that sit in front of an
app (nginx's `$request`, load balancers, browser history), so the question is
not "can we avoid putting a credential in the URL" -- we cannot -- but "what is
the credential worth once it leaks".

These tests pin that answer: a ticket can open a notification socket for about a
minute, and it can do nothing else. The two properties that make that true are
tested directly below -- REST endpoints reject it, and the socket rejects
everything that is not a ticket.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.security import create_refresh_token, create_ws_token, decode_token
from app.tests.conftest import auth_headers


def _mint_ticket(client, email="ticket_user@example.com"):
    headers = auth_headers(client, email=email)
    response = client.post("/auth/ws-ticket", headers=headers)
    assert response.status_code == 200, response.text
    return headers, response.json()


class TestTicketIssuance:
    def test_requires_authentication(self, client):
        response = client.post("/auth/ws-ticket")
        assert response.status_code == 401

    def test_response_shape(self, client):
        _, body = _mint_ticket(client, email="ticket_shape@example.com")
        assert body["token_type"] == "ws"
        assert body["expires_in"] == settings.WS_TOKEN_EXPIRE_SECONDS
        assert isinstance(body["ticket"], str) and body["ticket"].count(".") == 2

    def test_ticket_is_scoped_and_bound_to_the_user(self, client):
        headers, body = _mint_ticket(client, email="ticket_scope@example.com")
        user_id = client.get("/users/me", headers=headers).json()["id"]
        payload = decode_token(body["ticket"])
        assert payload["type"] == "ws"
        assert payload["sub"] == str(user_id)

    def test_ticket_lifetime_is_short_and_much_shorter_than_an_access_token(self):
        """Guard against the mitigation being quietly defeated by config.

        A ticket that lived as long as an access token would put us back where
        we started, so the relationship between the two is asserted rather than
        left to whoever next edits the settings.
        """
        assert settings.WS_TOKEN_EXPIRE_SECONDS <= 120
        assert settings.WS_TOKEN_EXPIRE_SECONDS < settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


class TestTicketIsWorthlessOutsideTheSocket:
    def test_rest_endpoint_rejects_a_ticket(self, client):
        """The property the whole remediation rests on.

        If a ticket could be replayed against the REST API, moving it out of
        the access token would have bought nothing -- a leaked query string
        would still be a leaked credential.
        """
        headers, body = _mint_ticket(client, email="ticket_replay@example.com")
        response = client.get("/users/me", headers={"Authorization": f"Bearer {body['ticket']}"})
        assert response.status_code == 401
        # And the real access token for the same user still works, so this is a
        # scope distinction and not a broken auth path.
        assert client.get("/users/me", headers=headers).status_code == 200

    def test_ticket_cannot_be_used_to_refresh(self, client):
        _, body = _mint_ticket(client, email="ticket_refresh@example.com")
        response = client.post("/auth/refresh", json={"refresh_token": body["ticket"]})
        assert response.status_code == 401


def _connect(client, token):
    """Open the notification socket with `token` in the query string."""
    return client.websocket_connect(f"/ws/notifications?token={token}")


class TestSocketAcceptsOnlyTickets:
    def test_access_token_is_rejected(self, client):
        headers = auth_headers(client, email="socket_access@example.com")
        access_token = headers["Authorization"].split(" ")[1]
        with pytest.raises(WebSocketDisconnect) as exc:
            with _connect(client, access_token):
                pass
        assert exc.value.code == 4401

    def test_refresh_token_is_rejected(self, client):
        headers = auth_headers(client, email="socket_refresh@example.com")
        user_id = client.get("/users/me", headers=headers).json()["id"]
        refresh = create_refresh_token(str(user_id))
        with pytest.raises(WebSocketDisconnect) as exc:
            with _connect(client, refresh):
                pass
        assert exc.value.code == 4401

    def test_expired_ticket_is_rejected(self, client, monkeypatch):
        """An expired ticket must not open a socket.

        Minted through the real endpoint with a negative lifetime, so this
        exercises the same path a client would hit a minute too late rather
        than hand-rolling a token.
        """
        headers = auth_headers(client, email="socket_expired@example.com")
        monkeypatch.setattr(settings, "WS_TOKEN_EXPIRE_SECONDS", -30)
        ticket = client.post("/auth/ws-ticket", headers=headers).json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with _connect(client, ticket):
                pass

    def test_ticket_signed_with_the_wrong_key_is_rejected(self, client):
        forged = jwt.encode(
            {
                "sub": "1",
                "type": "ws",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(seconds=30),
            },
            "not-the-real-secret-key",
            algorithm=settings.ALGORITHM,
        )
        with pytest.raises(WebSocketDisconnect):
            with _connect(client, forged):
                pass

    def test_ticket_with_the_right_type_but_no_such_user_is_rejected(self, client):
        orphan = create_ws_token("999999")
        with pytest.raises(WebSocketDisconnect):
            with _connect(client, orphan):
                pass

    def test_valid_ticket_opens_the_socket(self, client):
        """Positive control: the above rejections are scope, not a broken socket."""
        _, body = _mint_ticket(client, email="socket_valid@example.com")
        with _connect(client, body["ticket"]) as ws:
            assert ws.receive_json()["event"] == "unread_backlog"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class TestMalformedTokensDoNotEscapeAsErrors:
    def test_deeply_nested_payload_closes_the_socket_instead_of_raising(self, client):
        """Regression test for the PyJWT payload-recursion advisory (S6).

        A token whose payload segment is JSON nested ~20k deep used to make
        `json.loads` raise `RecursionError`, which is not a `ValueError` and so
        escaped every documented JWT error type. On an auth path that is an
        unauthenticated exception per request. PyJWT >= 2.15.0 converts it to a
        decode error; this asserts what the socket actually does with one.
        """
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url(b"[" * 20_000 + b"]" * 20_000)
        token = f"{header}.{payload}.{_b64url(b'forged-signature')}"

        with pytest.raises(WebSocketDisconnect):
            with _connect(client, token):
                pass

        # Still serving afterwards.
        assert client.get("/health").status_code == 200
