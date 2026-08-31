"""WebSocket routes + broadcast hub.

One endpoint per topic ``/ws/{topic}`` (docs/50-frontend/websocket-protocol.md). Connections
must present an allowed browser origin from ``FRONTEND_URL``; private VPS access control
is handled by the host network rather than a second application authentication layer.
A ``ConnectionManager`` fans messages out to clients subscribed to each topic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws import protocol

logger = logging.getLogger(__name__)

ALLOWED_TOPICS = frozenset(
    {"market-data", "stocks", "capture-status", "session", "historical-jobs"}
)

CLOSE_POLICY_VIOLATION = 1008

# Per-client send budget. These frames go out at 1 Hz, so a client that cannot absorb one
# within this window is not keeping up and is pruned rather than allowed to stall the
# topic. Deliberately far below the websocket ping timeout (~20-40 s), which is much too
# slow to protect a live dashboard.
SEND_TIMEOUT_S = 2.0


class ConnectionManager:
    """Tracks connected websockets per topic and broadcasts messages to them."""

    def __init__(self) -> None:
        self._topics: dict[str, set[WebSocket]] = defaultdict(set)
        # Observability for pruning, so a repeatedly-dropped client is visible.
        self.send_timeouts = 0
        self.send_failures = 0

    async def connect(self, topic: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._topics[topic].add(websocket)

    def disconnect(self, topic: str, websocket: WebSocket) -> None:
        subscribers = self._topics.get(topic)
        if subscribers is not None:
            subscribers.discard(websocket)

    def count(self, topic: str) -> int:
        # ``.get`` rather than ``[]``: a defaultdict would otherwise mint a permanent empty
        # entry for every topic ever merely *asked* about.
        return len(self._topics.get(topic) or ())

    async def broadcast(self, topic: str, message: dict) -> int:
        """Send ``message`` to every client on ``topic``; prune slow or dead sockets.

        The payload is serialized **once** and sent as text to each client. Using
        ``send_json`` would re-run ``json.dumps`` per client, and these frames are large
        (a full option grid) and sent every second, so per-client encoding wasted event-
        loop time proportional to the number of dashboards open.

        Every send is bounded by :data:`SEND_TIMEOUT_S`. Without that bound one client
        whose receive window is full (stalled proxy, wedged intermediary) blocked the
        ``await`` forever, and because the publisher awaits this call, **every** topic
        stopped updating for **every** client with no error and no counter. A client that
        exceeds its budget is pruned and the loop continues.
        """
        subscribers = self._topics.get(topic)
        if not subscribers:
            return 0
        text = json.dumps(message)
        dead: list[WebSocket] = []
        sent = 0
        for websocket in list(subscribers):
            try:
                await asyncio.wait_for(websocket.send_text(text), timeout=SEND_TIMEOUT_S)
                sent += 1
            except TimeoutError:
                self.send_timeouts += 1
                dead.append(websocket)
                logger.warning(
                    "pruning a websocket on %r that did not accept a frame within %.1fs "
                    "(%d send timeout(s) so far)",
                    topic,
                    SEND_TIMEOUT_S,
                    self.send_timeouts,
                )
            except Exception:  # noqa: BLE001 - a broken client shouldn't stop the rest
                self.send_failures += 1
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
        # Everything after registration lives under the finally, so no exit path can leave
        # the socket in ``_topics``: a failed handshake, ConnectionResetError, RuntimeError
        # from a half-closed socket, or task cancellation on shutdown all used to leak an
        # entry that was then sent to once per second forever.
        try:
            await websocket.send_json(protocol.session_status("connected", {"topic": topic}))
            while True:
                # We are push-only; reading just keeps the socket open and detects close.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass  # normal client-initiated close
        except asyncio.CancelledError:
            raise  # server shutdown — still deregistered by the finally
        except Exception:  # noqa: BLE001 - any transport failure ends this connection
            logger.debug("websocket on %r ended with an error", topic, exc_info=True)
        finally:
            hub.disconnect(topic, websocket)

    return router
