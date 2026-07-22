"""WebSocket routes + broadcast hub.

One endpoint per topic ``/ws/{topic}`` (docs/50-frontend/websocket-protocol.md). Connections
must present an allowed browser origin from ``FRONTEND_URL``; private VPS access control
is handled by the host network rather than a second application authentication layer.
A ``ConnectionManager`` fans messages out to clients subscribed to each topic.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws import protocol

logger = logging.getLogger(__name__)

ALLOWED_TOPICS = frozenset(
    {"market-data", "stocks", "capture-status", "session", "historical-jobs"}
)

CLOSE_POLICY_VIOLATION = 1008


class ConnectionManager:
    """Tracks connected websockets per topic and broadcasts messages to them."""

    def __init__(self) -> None:
        self._topics: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, topic: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._topics[topic].add(websocket)

    def disconnect(self, topic: str, websocket: WebSocket) -> None:
        self._topics[topic].discard(websocket)

    def count(self, topic: str) -> int:
        return len(self._topics[topic])

    async def broadcast(self, topic: str, message: dict) -> int:
        """Send ``message`` to every client on ``topic``; prune dead sockets."""
        dead: list[WebSocket] = []
        sent = 0
        for websocket in list(self._topics[topic]):
            try:
                await websocket.send_json(message)
                sent += 1
            except Exception:  # noqa: BLE001 - a broken client shouldn't stop the rest
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(topic, websocket)
        return sent


def create_ws_router(hub: ConnectionManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/{topic}")
    async def ws_endpoint(websocket: WebSocket, topic: str) -> None:
        if topic not in ALLOWED_TOPICS:
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return
        settings = getattr(websocket.app.state, "settings", None)
        allowed_origins = settings.cors_origins if settings is not None else []
        if websocket.headers.get("origin") not in allowed_origins:
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return

        await hub.connect(topic, websocket)
        await websocket.send_json(protocol.session_status("connected", {"topic": topic}))
        try:
            while True:
                # We are push-only; reading just keeps the socket open and detects close.
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(topic, websocket)

    return router
