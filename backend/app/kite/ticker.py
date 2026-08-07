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

from app.kite.errors import is_authentication_error
from app.session import now_ms

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

# Given the token that just failed, return a fresh access token (or ``None`` if the
# broker has nothing authenticated to hand back). Runs in a worker thread, so it may
# block on network I/O (the calspread HTTP fetch).
TokenProvider = Callable[[str | None], str | None]


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
        token_provider: TokenProvider | None = None,
        queue_maxsize: int = 10_000,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.tokens = list(tokens)
        self._factory = ticker_factory or _default_ticker_factory
        self._token_provider = token_provider
        self._clock = clock
        self.queue: asyncio.Queue[list[dict]] = asyncio.Queue(maxsize=queue_maxsize)
        self.auth_failed = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ticker: Ticker | None = None
        self.connected = False
        # Observability counters.
        self.batches_received = 0
        self.ticks_received = 0
        self.dropped_batches = 0
        self.reconnects = 0
        # Token lifecycle: when the current in-memory token was set + refresh telemetry.
        self._token_set_ms: int = self._clock()
        self.token_refreshes = 0
        self.last_token_refresh_ms: int | None = None

    # -- thread-side callbacks (run on the KiteTicker thread) ---------------- #

    def _on_ticks(self, ws: Any, ticks: list[dict]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, ticks)

    def _on_connect(self, ws: Any, response: Any = None) -> None:
        logger.info("ticker connected; subscribing %d tokens (full mode)", len(self.tokens))
        try:
            ws.subscribe(self.tokens)
            ws.set_mode(MODE_FULL, self.tokens)
        except Exception as exc:  # pragma: no cover - defensive; SDK-specific
            if is_authentication_error(exc):
                self._signal_auth_failure()
                logger.warning("ticker subscription rejected by Kite authentication")
            else:
                logger.exception("subscribe/set_mode failed")
            self.connected = False
            return
        self.connected = True

    def _on_close(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.warning("ticker closed: code=%s reason=%s", code, reason)
        if is_authentication_error(code=code, reason=reason):
            self._signal_auth_failure()
        self.connected = False

    def _on_error(self, ws: Any, code: Any = None, reason: Any = None) -> None:
        logger.error("ticker error: code=%s reason=%s", code, reason)
        if is_authentication_error(code=code, reason=reason):
            self._signal_auth_failure()

    def _signal_auth_failure(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.auth_failed.set)

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
            logger.error(
                "ticker ingestion queue overflow; dropped oldest batch (count=%d)",
                self.dropped_batches,
            )
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(ticks)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()
        self.auth_failed.clear()

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Create the ticker, wire callbacks, and connect on a background thread."""
        self.bind_loop(loop)
        self._start_ticker()

    def _start_ticker(self) -> None:
        """Instantiate a fresh ticker, wire callbacks, and connect (threaded)."""
        ticker = self._factory(self.api_key, self.access_token)
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        self._ticker = ticker
        ticker.connect(threaded=True)

    def reconnect(self) -> None:
        """Tear down the current ticker and start a fresh one (self-driven recovery).

        Called from the event-loop thread when the freshness monitor flags a stall
        the SDK's own reconnect did not recover (e.g. a half-open socket, or a feed
        that keeps the connection up but stops sending fresh quotes). A brand-new
        socket re-subscribes every token in ``full`` mode via ``_on_connect``.

        The stale ticker (which holds its own copy of the previous access token) is
        closed *and* dereferenced before the replacement is created, so it — and any
        superseded token — becomes collectable immediately rather than lingering on the
        heap for an unbounded time.
        """
        self.reconnects += 1
        logger.warning("self-driven ticker reconnect (attempt %d)", self.reconnects)
        old = self._ticker
        self._ticker = None  # drop the bridge's reference before replacing
        if old is not None:
            try:
                old.close()
            except Exception:  # pragma: no cover - defensive; SDK-specific
                logger.exception("error closing stale ticker during reconnect")
        del old  # let the superseded ticker (and its token copy) be GC'd promptly
        self.connected = False
        try:
            self._start_ticker()
        except Exception:  # pragma: no cover - defensive; SDK-specific
            logger.exception("failed to start replacement ticker during reconnect")

    def set_token_provider(self, token_provider: TokenProvider | None) -> None:
        """Wire (or replace) the callable used to fetch a fresh token.

        Retained for the reconnect drill and tests. The live capture path no longer uses
        it: recovery is restart-first (see ``CaptureEngine.observe_feed_health``), because
        the in-process token-refresh ladder this once fed never obtained a token on any
        recorded trading day while destroying the day's persisted session each attempt.
        """
        self._token_provider = token_provider

    def _apply_new_token(self, token: str) -> None:
        """Adopt a freshly fetched token, dropping the reference to the old one."""
        self.access_token = token  # rebinds away from the previous (dead) token
        self._token_set_ms = self._clock()
        self.token_refreshes += 1
        self.last_token_refresh_ms = self._token_set_ms

    def token_age_ms(self, now: int | None = None) -> int:
        """Milliseconds since the in-memory access token was last (re)set."""
        reference = now if now is not None else self._clock()
        return max(0, reference - self._token_set_ms)

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
