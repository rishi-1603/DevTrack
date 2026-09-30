"""Schemas for authentication tokens."""
from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    type: str


class WsTicket(BaseModel):
    """A short-lived credential for the WebSocket handshake only.

    Returned by `POST /auth/ws-ticket`. Deliberately NOT interchangeable with
    `Token`: the `type` is "ws", which every REST dependency rejects, so a
    ticket that leaks out of a proxy log cannot be replayed against the API.
    """

    ticket: str
    expires_in: int
    token_type: str = "ws"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
