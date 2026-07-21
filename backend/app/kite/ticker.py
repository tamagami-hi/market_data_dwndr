"""KiteTicker -> asyncio bridge.

``KiteTicker`` runs its own background thread and invokes ``on_ticks`` from there. We
bridge those callbacks onto the asyncio event loop with ``call_soon_threadsafe`` so
the rest of the pipeline (apply, 1 Hz snapshot) stays single-threaded on the loop
(docs/10-architecture/concurrency-and-gil.md).

The concrete ``KiteTicker`` is created by an injected factory, so the bridge is unit-
testable without a network connection. On connect we subscribe the full token set and
switch it to ``full`` mode (delivers OI, OHLC, and 5-level depth).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MODE_FULL = "full"


class Ticker(Protocol):
    """Minimal surface of ``kiteconnect.KiteTicker`` the bridge relies on."""

    on_ticks: Callable[..., None]
    on_connect: Callable[..., None]
    on_close: Callable[..., None]
    on_error: Callable[..., None]
    on_reconnect: Callable[..., None]

    def connect(self, threaded: bool = ...) -> None: ...
    def subscribe(self, tokens: list[int]) -> None: ...
    def set_mode(self, mode: str, tokens: list[int]) -> None: ...
    def close(self, *args: Any, **kwargs: Any) -> None: ...


TickerFactory = Callable[[str, str], Ticker]


def _default_ticker_factory(api_key: str, access_token: str) -> Ticker:
    from kiteconnect import KiteTicker

    return KiteTicker(api_key, access_token)


class TickerBridge:
    """Bridges KiteTicker thread callbacks into an ``asyncio.Queue`` of tick batches."""

    def __init__(
        self,
        api_key: str,
        access_token: str,
        tokens: list[int],
        *,
        ticker_factory: TickerFactory | None = None,
        queue_maxsize: int = 10_000,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.tokens = list(tokens)
        self._factory = ticker_factory or _default_ticker_factory
        self.queue: asyncio.Queue[list[dict]] = asyncio.Queue(maxsize=queue_maxsize)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ticker: Ticker | None = None
        self.connected = False
        # Observability counters.
        self.batches_received = 0
        self.ticks_received = 0
        self.dropped_batches = 0

    # -- thread-side callbacks (run on the KiteTicker thread) ---------------- #

    def _on_ticks(self, ws: Any, ticks: list[dict]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, ticks)

    def _on_connect(self, ws: Any, response: Any = None) -> None:
        logger.info("ticker connected; subscribing %d tokens (full mode)", len(self.tokens))
        try:
            ws.subscribe(self.tokens)
            ws.set_mode(MODE_FULL, self.tokens)
        except Exception:  # pragma: no cover - defensive; SDK-specific
            logger.exception("subscribe/set_mode failed")
        self.connected = True

    def _on_close(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.warning("ticker closed: code=%s reason=%s", code, reason)
        self.connected = False

    def _on_error(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.error("ticker error: code=%s reason=%s", code, reason)

    def _on_reconnect(self, ws: Any, attempts: Any = None) -> None:
        logger.warning("ticker reconnecting (attempt %s)", attempts)

    # -- loop-side --------------------------------------------------------- #

    def _enqueue(self, ticks: list[dict]) -> None:
        """Runs on the event loop thread (via call_soon_threadsafe)."""
        self.batches_received += 1
        self.ticks_received += len(ticks)
        try:
            self.queue.put_nowait(ticks)
        except asyncio.QueueFull:
            # 1 Hz consumer should never fall behind; drop oldest to stay live.
            self.dropped_batches += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(ticks)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Create the ticker, wire callbacks, and connect on a background thread."""
        self.bind_loop(loop)
        ticker = self._factory(self.api_key, self.access_token)
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        self._ticker = ticker
        ticker.connect(threaded=True)

    def stop(self) -> None:
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:  # pragma: no cover - defensive
                logger.exception("error closing ticker")
        self.connected = False

    async def batches(self):
        """Async generator yielding tick batches as they arrive."""
        while True:
            yield await self.queue.get()
