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

# How long a freshly started ticker may take to report ``on_connect`` before the caller
# treats the socket as dead. A healthy connect is observed in ~1 s in production, so this
# is generous; the point is that it is *bounded* (see ``TickerBridge.await_connected``).
CONNECT_TIMEOUT_S = 15.0

# Bounds on kiteconnect's own internal retry, pinned rather than inherited so the
# in-process reconnect rung can take over from a socket the SDK cannot recover.
SDK_RECONNECT_MAX_TRIES = 5
SDK_RECONNECT_MAX_DELAY_S = 10

# A recovery reconnect must confirm quickly or be retried: a healthy connect+subscribe is
# ~1 s, so this is generous while keeping the whole ladder well inside the stale-feed
# deadline. Deliberately shorter than CONNECT_TIMEOUT_S, which covers a cold start.
RECOVERY_CONFIRM_TIMEOUT_S = 5.0

# Confirmed in-process reconnects attempted before declaring the feed unrecoverable and
# handing over to the process-restart escalation.
RECOVERY_MAX_ATTEMPTS = 3


class TickerConnectFailed(RuntimeError):
    """Raised when a ticker never reports a live subscription within the deadline.

    The failure this exists for is silent: ``kiteconnect.KiteTicker.connect(threaded=True)``
    only starts the process-global Twisted reactor ``if not reactor.running``, and its
    ``close()`` never stops that reactor. So the *second* ticker created in one process
    used to schedule ``connectWS`` from the main thread onto a reactor already running in
    another thread — an unsynchronised cross-thread reactor call that is never serviced.
    No exception, no callback, no ticks: capture ran a full 1 Hz grid against a socket that
    had never connected. ``_connect_ticker`` fixes the cause; this exception makes any
    residual variant loud instead of costing a 295-second hole.
    """


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

    ticker = KiteTicker(api_key, access_token)
    # Pin the SDK's internal retry instead of inheriting whatever its defaults are: the
    # in-process reconnect rung (see ``CaptureEngine.observe_feed_health``) needs the SDK
    # to give up reasonably promptly so a replacement socket can be built, rather than
    # retrying opaquely for minutes behind a feed we already know is dead.
    try:
        ticker.reconnect_max_tries = SDK_RECONNECT_MAX_TRIES
        ticker.reconnect_max_delay = SDK_RECONNECT_MAX_DELAY_S
    except Exception:  # noqa: BLE001 - never let telemetry tuning break the connect
        logger.debug("could not pin ticker reconnect parameters", exc_info=True)
    return ticker


def _connect_ticker(ticker: Ticker) -> None:
    """Connect ``ticker``, correctly servicing an already-running global reactor.

    ``connect(threaded=True)`` is safe only when it is the *first* connect in the process,
    because it both schedules the connection and starts the reactor. Once the reactor is
    running, the only thread-safe way to touch it is ``callFromThread``, so the connect is
    performed on the reactor thread itself. Without this, every capture session after the
    first in a given process gets a dead feed.

    Falls back to a plain threaded connect when Twisted is absent (unit-test fakes).
    """
    try:
        from twisted.internet import reactor
    except Exception:  # noqa: BLE001 - no Twisted (fake tickers in tests)
        ticker.connect(threaded=True)
        return
    if getattr(reactor, "running", False):
        # Reactor already up from an earlier ticker in this process: hand the connect to
        # the reactor thread rather than calling into it from here.
        logger.info("reactor already running; connecting ticker on the reactor thread")
        reactor.callFromThread(ticker.connect, threaded=True)
        return
    ticker.connect(threaded=True)


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
        # Set once a ticker has actually reported a live, subscribed socket. Nothing used
        # to read ``connected``, so a socket that never came up was indistinguishable from
        # a healthy one until the freshness monitor noticed 5 s later — and then only the
        # 40 s escalation could act. Callers now await this before entering the grid.
        #
        # "Confirmed" means all three of: transport connected, ``subscribe(tokens)``
        # accepted, and ``set_mode(FULL, tokens)`` accepted. It is set in exactly one
        # place — the success path of ``_on_connect`` — so an open-but-unsubscribed socket
        # can never satisfy it.
        self.connect_confirmed = asyncio.Event()
        # Set when the confirmed-reconnect ladder is exhausted: the socket is known to be
        # unusable *now*, so capture escalates immediately instead of waiting for the
        # freshness deadline.
        self.feed_unrecoverable = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ticker: Ticker | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self.connected = False
        # Observability counters.
        self.batches_received = 0
        self.ticks_received = 0
        self.dropped_batches = 0
        # Batches discarded because no event loop was bound (shutdown races). Previously
        # dropped in silence, which is indistinguishable from a quiet market.
        self.dropped_unbound_batches = 0
        self.reconnects = 0
        self.reconnects_confirmed = 0
        self.reconnect_failures = 0
        self.subscribe_failures = 0
        self.connect_attempts = 0
        self.connects_confirmed = 0
        self.last_connect_ms: int | None = None
        # Token lifecycle: when the current in-memory token was set + refresh telemetry.
        self._token_set_ms: int = self._clock()
        self.token_refreshes = 0
        self.last_token_refresh_ms: int | None = None

    # -- thread-side callbacks (run on the KiteTicker thread) ---------------- #

    def _on_ticks(self, ws: Any, ticks: list[dict]) -> None:
        loop = self._loop
        if loop is None:
            # No loop bound (pre-start, or after stop()): the batch cannot be delivered.
            # Counted rather than dropped in silence, which read as a quiet market.
            self.dropped_unbound_batches += 1
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, ticks)
        except RuntimeError:
            # Loop already closed — a shutdown race. Raising here would surface as an
            # unobserved exception inside the KiteTicker thread and could take that thread
            # down, so record it and let the shutdown proceed.
            self.dropped_unbound_batches += 1
            self._loop = None

    def _on_connect(self, ws: Any, response: Any = None) -> None:
        logger.info("ticker connected; subscribing %d tokens (full mode)", len(self.tokens))
        try:
            ws.subscribe(self.tokens)
            ws.set_mode(MODE_FULL, self.tokens)
        except Exception as exc:  # pragma: no cover - defensive; SDK-specific
            # The transport is up but nothing is subscribed: ``on_close`` will never fire,
            # so the SDK sees no reason to reconnect and this socket would sit open and
            # silent forever. Tear it down and rebuild with the same confirmation
            # semantics instead of leaving a zombie behind.
            self.subscribe_failures += 1
            self.connected = False
            if is_authentication_error(exc):
                self._signal_auth_failure()
                logger.warning("ticker subscription rejected by Kite authentication")
                return  # a token problem cannot be fixed by another socket
            logger.exception("subscribe/set_mode failed; tearing down the unsubscribed socket")
            self.request_recovery("subscribe/set_mode failed")
            return
        self.connected = True
        self.connects_confirmed += 1
        self.last_connect_ms = self._clock()
        self._signal_connected()

    def _signal_connected(self) -> None:
        """Publish the confirmed connection to the event loop (thread-safe)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.connect_confirmed.set)
        else:  # no loop bound (unit tests calling start() outside asyncio)
            self.connect_confirmed.set()

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
        self.connect_attempts += 1
        self.connect_confirmed.clear()
        _connect_ticker(ticker)

    async def await_connected(self, timeout: float = CONNECT_TIMEOUT_S) -> None:
        """Block until the ticker reports a live subscription, or fail loudly.

        This is the guard that turns the old silent dead feed into an immediate, named
        failure. Without it the capture grid would start against a socket that never
        connected and write frames from empty tables until the freshness monitor and then
        the 40 s escalation deadline eventually noticed.
        """
        if self.connect_confirmed.is_set():
            return
        try:
            await asyncio.wait_for(self.connect_confirmed.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise TickerConnectFailed(
                f"ticker did not confirm a live subscription within {timeout:g}s "
                f"(attempt {self.connect_attempts}, {len(self.tokens)} tokens)"
            ) from exc

    def _teardown_ticker(self) -> None:
        """Close and dereference the current ticker so no zombie socket is left open.

        The stale ticker holds its own copy of the previous access token, so it is closed
        *and* dereferenced before any replacement is built — it (and the superseded token)
        becomes collectable immediately rather than lingering on the heap.
        """
        old = self._ticker
        self._ticker = None  # drop the bridge's reference before replacing
        self.connected = False
        self.connect_confirmed.clear()
        if old is not None:
            try:
                old.close()
            except Exception:  # pragma: no cover - defensive; SDK-specific
                logger.exception("error closing stale ticker")
        del old  # let the superseded ticker (and its token copy) be GC'd promptly

    async def reconnect_confirmed(self, timeout: float | None = None) -> None:
        """Replace the socket and wait for a **confirmed** subscription.

        Raises :class:`TickerConnectFailed` unless the replacement reports transport
        connected *and* ``subscribe`` *and* ``set_mode(FULL)`` accepted. A reconnect that
        merely started a ticker is not a success — that was the old behaviour, and it let a
        dead replacement look like recovery until the freshness deadline.

        ``timeout`` defaults to :data:`RECOVERY_CONFIRM_TIMEOUT_S`, resolved at call time so
        the bound is configurable in one place.
        """
        self._teardown_ticker()
        self._start_ticker()
        await self.await_connected(
            RECOVERY_CONFIRM_TIMEOUT_S if timeout is None else timeout
        )
        self.reconnects_confirmed += 1

    def request_recovery(self, reason: str) -> None:
        """Ask the event loop to run the confirmed-reconnect ladder. Thread-safe.

        Callable from the KiteTicker/reactor thread (``_on_connect``'s failure path), which
        must not touch the loop directly.
        """
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._schedule_recovery, reason)
        except RuntimeError:  # pragma: no cover - loop closed during shutdown
            self._loop = None

    def _schedule_recovery(self, reason: str) -> None:
        """Start the recovery ladder unless one is already running (loop thread only)."""
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        self._recovery_task = asyncio.create_task(
            self._recover_confirmed(reason), name="ticker-recovery"
        )

    async def _recover_confirmed(self, reason: str) -> None:
        """Bounded ladder: confirmed reconnect, then hand over to the restart escalation."""
        for attempt in range(1, RECOVERY_MAX_ATTEMPTS + 1):
            try:
                await self.reconnect_confirmed()
            except TickerConnectFailed as exc:
                self.reconnect_failures += 1
                logger.warning(
                    "confirmed reconnect %d/%d after %s failed: %s",
                    attempt,
                    RECOVERY_MAX_ATTEMPTS,
                    reason,
                    exc,
                )
                continue
            except Exception:  # noqa: BLE001 - defensive; SDK-specific
                self.reconnect_failures += 1
                logger.exception(
                    "confirmed reconnect %d/%d after %s raised",
                    attempt,
                    RECOVERY_MAX_ATTEMPTS,
                    reason,
                )
                continue
            logger.info(
                "ticker recovered after %s: confirmed subscription on attempt %d",
                reason,
                attempt,
            )
            return
        # Every rung failed. Make it known now so capture escalates to a clean restart
        # instead of waiting for the stale-feed deadline to notice.
        logger.error(
            "ticker could not confirm a subscription after %d attempt(s) following %s; "
            "escalating to a process restart",
            RECOVERY_MAX_ATTEMPTS,
            reason,
        )
        self._teardown_ticker()
        self.feed_unrecoverable.set()

    def reconnect(self) -> None:
        """Replace the current socket with a **confirmed** one (self-driven recovery).

        Called from the event-loop thread when the freshness monitor flags a stall the
        SDK's own reconnect did not recover (e.g. a half-open socket, or a feed that keeps
        the connection up but stops sending fresh quotes).

        Non-blocking by design: the caller is the 1 Hz grid loop, which must not await a
        socket handshake. The confirmation happens on the recovery task, and its failure
        sets :attr:`feed_unrecoverable` so the next grid tick escalates.
        """
        self.reconnects += 1
        logger.warning("self-driven ticker reconnect (attempt %d)", self.reconnects)
        if self._loop is None:
            # No loop bound (unit rigs / sync callers): swap in place, unconfirmed.
            self._teardown_ticker()
            try:
                self._start_ticker()
            except Exception:  # pragma: no cover - defensive; SDK-specific
                logger.exception("failed to start replacement ticker during reconnect")
            return
        self._schedule_recovery("stale feed")

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
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task = None
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:  # pragma: no cover - defensive
                logger.exception("error closing ticker")
        self.connected = False
        # Unbind the loop last: ``_on_ticks`` may still fire from the KiteTicker thread
        # after close(), and calling into a closing loop is what raises unobserved
        # RuntimeErrors on that thread.
        self._loop = None

    async def batches(self):
        """Async generator yielding tick batches as they arrive."""
        while True:
            yield await self.queue.get()
