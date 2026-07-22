"""Tests for the WebSocket broadcast hub and topic routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ws.routes import ConnectionManager, create_ws_router


class FakeWebSocket:
    def __init__(self, fail_on_send: bool = False) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed_code: int | None = None
        self._fail = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self._fail:
            raise RuntimeError("broken client")
        self.sent.append(message)


# --- ConnectionManager unit tests --------------------------------------------


async def test_broadcast_reaches_topic_subscribers_only():
    hub = ConnectionManager()
    a, b, c = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
    await hub.connect("market-data", a)
    await hub.connect("market-data", b)
    await hub.connect("capture-status", c)

    sent = await hub.broadcast("market-data", {"type": "X"})
    assert sent == 2
    assert a.sent == [{"type": "X"}] and b.sent == [{"type": "X"}]
    assert c.sent == []  # different topic


async def test_broadcast_prunes_dead_sockets():
    hub = ConnectionManager()
    good, bad = FakeWebSocket(), FakeWebSocket(fail_on_send=True)
    await hub.connect("session", good)
    await hub.connect("session", bad)
    assert hub.count("session") == 2
    sent = await hub.broadcast("session", {"type": "Heartbeat"})
    assert sent == 1
    assert hub.count("session") == 1  # dead pruned


# --- route integration tests -------------------------------------------------


class FakeSettings:
    cors_origins = ["http://frontend.test"]


def _client() -> TestClient:
    app = FastAPI()
    app.state.settings = FakeSettings()
    app.include_router(create_ws_router(ConnectionManager()))
    return TestClient(app)


def test_connect_from_allowed_origin_receives_welcome():
    client = _client()
    with client.websocket_connect(
        "/ws/market-data", headers={"Origin": "http://frontend.test"}
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "SessionStatus"
        assert msg["payload"]["phase"] == "connected"
        assert msg["payload"]["diagnostics"]["topic"] == "market-data"


def test_connect_from_allowed_origin_needs_no_cookie():
    client = _client()
    with client.websocket_connect(
        "/ws/market-data", headers={"Origin": "http://frontend.test"}
    ) as ws:
        assert ws.receive_json()["payload"]["phase"] == "connected"


def test_query_string_token_is_ignored():
    client = _client()
    with client.websocket_connect(
        "/ws/market-data?token=legacy-secret",
        headers={"Origin": "http://frontend.test"},
    ) as ws:
        assert ws.receive_json()["payload"]["phase"] == "connected"


def test_untrusted_websocket_origin_is_rejected() -> None:
    client = _client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/market-data", headers={"Origin": "http://malicious.test"}
        ) as ws:
            ws.receive_json()


def test_unknown_topic_is_rejected():
    client = _client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/execution", headers={"Origin": "http://frontend.test"}
        ) as ws:
            ws.receive_json()
