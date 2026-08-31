"""Non-blocking PUSH publisher from TickVault into MARKET_DATA_BUS.

The contract this module exists to enforce: **the bus is an additional output, never a
dependency**. Capture, BIN writing and feed-health must behave identically whether the bus
is absent, dead, restarting, full, or has no subscribers.

That is achieved by three rules, all of them local to this file:

1. ``zmq.DONTWAIT`` on every send. A bus-side or consumer-side stall raises ``zmq.Again``
   immediately instead of parking the tick thread.
2. A bounded ``SNDHWM``. When the outbound queue is full the message is dropped and
   counted; it is never buffered without limit and never awaited.
3. Every public method is total — no exception escapes to the caller. A publisher fault
   degrades bus telemetry and nothing else, because the caller is the tick-ingestion path
   whose death would freeze capture.

``zmq.LINGER`` is 0 so a close during shutdown cannot block on undelivered messages, and
the socket is created and used on one thread only (a ZMQ socket is not thread-safe; the
context is).

Transport is TCP rather than ``ipc://``. See ``market_data_bus/README.md`` for the
evidence: the participating containers run as three different uids (``10001:10001``,
``node``, ``101:101``) across independent Compose projects with no shared network, and
TickVault's container root filesystem is read-only — so a shared socket directory would
need world-writable permissions and would strand stale socket files across hard restarts.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any

from app.bus.normalize import normalize_tick
from app.bus.protocol import (
    MAX_TICKS_PER_MESSAGE,
    MSG_SESSION,
    MSG_TICK_BATCH,
    TOPIC_SESSION,
    TOPIC_TICK,
    BusHeader,
    InstrumentInfo,
    encode_header,
    encode_session,
    encode_tick_batch,
)

logger = logging.getLogger(__name__)

_DONTWAIT: int = 0
_AGAIN: tuple[type[BaseException], ...] = ()
try:
    import zmq as _zmq

    _DONTWAIT = int(_zmq.DONTWAIT)
    _AGAIN = (_zmq.Again,)
except Exception:  # noqa: BLE001 - importable without pyzmq (bus stays disabled)
    pass

DEFAULT_SNDHWM = 10_000
DEFAULT_QUEUE_MAXSIZE = 2_048
_ERROR_LOG_INTERVAL_MS = 30_000
_STOP = object()


class BusPublisher:
    """Owns one PUSH socket and the counters the monitor reports.

    ``epoch`` is the producer run identity: it is stamped once at construction, so a
    consumer sees a new epoch whenever TickVault restarts and can reset its sequence
    expectations rather than reporting a gigantic gap.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        sndhwm: int = DEFAULT_SNDHWM,
        producer: str = "tickvault",
        clock=None,
        socket_factory=None,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        worker: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.sndhwm = max(1, int(sndhwm))
        self.producer = producer
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._socket_factory = socket_factory
        self._socket: Any | None = None
        self._context: Any | None = None
        self.epoch = self._clock()
        self.sequence = 0
        self.connected = False
        self.publish_total = 0
        self.publish_dropped = 0
        self.publish_errors = 0
        self.ticks_published = 0
        self.ticks_skipped = 0
        self.queue_dropped = 0
        self.session_publish_total = 0
        self.last_success_ms: int | None = None
        self._last_error_log_ms: int | None = None
        self._last_error: str | None = None
        self._use_worker = worker
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(queue_maxsize)))
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()

    def open(self) -> bool:
        """Create and connect the PUSH socket. Returns False on failure, never raises."""
        try:
            if self._socket_factory is not None:
                self._socket = self._socket_factory()
            else:
                import zmq

                self._context = zmq.Context.instance()
                socket = self._context.socket(zmq.PUSH)
                socket.setsockopt(zmq.SNDHWM, self.sndhwm)
                socket.setsockopt(zmq.LINGER, 0)
                socket.connect(self.endpoint)
                self._socket = socket
            self.connected = True
            if self._use_worker:
                self._start_worker()
            logger.info(
                "market-data bus publisher connected to %s (SNDHWM=%d, epoch=%d)",
                self.endpoint,
                self.sndhwm,
                self.epoch,
            )
            return True
        except BaseException as exc:  # noqa: BLE001 - the bus must never break capture
            self.connected = False
            self.publish_errors += 1
            self._note_error(exc)
            return False

    def _start_worker(self) -> None:
        """Run encode+send on a dedicated thread so the tick path pays only an enqueue.

        The socket is created on the caller's thread but touched **only** here afterwards
        (a ZMQ socket is not thread-safe; a Context is). ``publish_ticks`` never encodes
        and never sends, so the cost the tick consumer sees is one bounded queue put.
        """
        self._worker = threading.Thread(
            target=self._drain, name="bus-publisher", daemon=True
        )
        self._worker.start()

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            kind, payload = item
            try:
                if kind == "ticks":
                    self._encode_and_send_ticks(payload)
                else:
                    self._encode_and_send_session(payload)
            except BaseException:  # noqa: BLE001 - a worker fault must not end the worker
                self.publish_errors += 1
                logger.debug("bus worker iteration failed", exc_info=True)

    def close(self) -> None:
        self._stopping.set()
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
            worker.join(timeout=2.0)
        socket, self._socket = self._socket, None
        self.connected = False
        if socket is None:
            return
        try:
            socket.close(linger=0)
        except TypeError:
            try:
                socket.close()
            except BaseException:  # noqa: BLE001
                logger.debug("bus socket close failed", exc_info=True)
        except BaseException:  # noqa: BLE001
            logger.debug("bus socket close failed", exc_info=True)

    def publish_ticks(self, ticks: list[dict] | tuple[dict, ...]) -> bool:
        """Hand accepted ticks to the bus. Returns True if the work was accepted.

        On the tick-consumer thread this is deliberately O(1): a bounded queue put and
        nothing else. Normalization, encoding and the socket send all happen on the worker
        thread, so neither serialization CPU nor a stalled socket can show up as capture
        latency. When the queue is full the batch is dropped and counted — the bus is a
        distribution output, so shedding it is always preferable to delaying capture.

        ``ticks`` must already have passed TickVault's ingestion contract; this method does
        not decide validity, it only forwards what capture accepted.
        """
        if not ticks or self._socket is None:
            return False
        if not self._use_worker:
            self._encode_and_send_ticks(list(ticks))
            return True
        try:
            self._queue.put_nowait(("ticks", list(ticks)))
        except queue.Full:
            self.queue_dropped += 1
            return False
        return True

    def publish_session(
        self,
        *,
        trading_date: str,
        instruments: list[InstrumentInfo] | tuple[InstrumentInfo, ...],
    ) -> bool:
        """Publish the token -> instrument map a consumer needs to interpret ticks."""
        if self._socket is None:
            return False
        item = (trading_date, list(instruments))
        if not self._use_worker:
            self._encode_and_send_session(item)
            return True
        try:
            self._queue.put_nowait(("session", item))
        except queue.Full:
            self.queue_dropped += 1
            return False
        return True

    def _encode_and_send_ticks(self, ticks: list[dict]) -> bool:
        batch = []
        for tick in ticks:
            try:
                batch.append(normalize_tick(tick))
            except Exception:  # noqa: BLE001 - one bad tick, not a dead publisher
                self.ticks_skipped += 1
        if not batch:
            return False
        sent_any = False
        for start in range(0, len(batch), MAX_TICKS_PER_MESSAGE):
            chunk = batch[start : start + MAX_TICKS_PER_MESSAGE]
            if self._send(TOPIC_TICK, MSG_TICK_BATCH, len(chunk), encode_tick_batch(chunk)):
                self.ticks_published += len(chunk)
                sent_any = True
        return sent_any

    def _encode_and_send_session(self, item: tuple[str, list[InstrumentInfo]]) -> bool:
        trading_date, instruments = item
        payload = encode_session(
            trading_date=trading_date,
            producer=self.producer,
            epoch=self.epoch,
            instruments=instruments,
        )
        if self._send(TOPIC_SESSION, MSG_SESSION, 0, payload):
            self.session_publish_total += 1
            return True
        return False

    def _send(self, topic: bytes, message_type: int, count: int, payload: bytes) -> bool:
        socket = self._socket
        if socket is None:
            return False
        sequence = self.sequence + 1
        header = encode_header(
            BusHeader(
                epoch=self.epoch,
                sequence=sequence,
                publish_unix_ns=time.time_ns(),
                message_type=message_type,
                count=count,
            )
        )
        try:
            socket.send_multipart([topic, header, payload], flags=_DONTWAIT)
        except _AGAIN:
            self.publish_dropped += 1
            return False
        except BaseException as exc:  # noqa: BLE001 - never propagate into capture
            self.publish_errors += 1
            self._note_error(exc)
            return False
        self.sequence = sequence
        self.publish_total += 1
        self.last_success_ms = self._clock()
        return True

    def _note_error(self, exc: BaseException) -> None:
        self._last_error = type(exc).__name__
        now = self._clock()
        if (
            self._last_error_log_ms is not None
            and now - self._last_error_log_ms < _ERROR_LOG_INTERVAL_MS
        ):
            return
        self._last_error_log_ms = now
        logger.warning(
            "market-data bus publish failed (%s); capture is unaffected "
            "(%d error(s), %d drop(s) so far)",
            self._last_error,
            self.publish_errors,
            self.publish_dropped,
        )

    def status(self) -> dict:
        """Telemetry for the capture monitor. Reports only what can be measured.

        Notably absent: any claim about subscriber delivery. ZMQ PUB/SUB cannot prove a
        subscriber received anything, so no such field is invented here.
        """
        now = self._clock()
        return {
            "enabled": True,
            "endpoint": self.endpoint,
            "connected": self.connected,
            "epoch": self.epoch,
            "sequence": self.sequence,
            "sndhwm": self.sndhwm,
            "bus_publish_total": self.publish_total,
            "bus_publish_dropped": self.publish_dropped,
            "bus_publish_errors": self.publish_errors,
            "bus_ticks_published": self.ticks_published,
            "bus_ticks_skipped": self.ticks_skipped,
            "bus_queue_dropped": self.queue_dropped,
            "bus_queue_depth": self._queue.qsize(),
            "bus_session_publish_total": self.session_publish_total,
            "bus_last_success_age_ms": (
                None if self.last_success_ms is None else max(0, now - self.last_success_ms)
            ),
            "last_error": self._last_error,
        }


class NullBusPublisher:
    """Used when the bus is disabled, so callers need no conditionals.

    With this in place TickVault's behaviour is indistinguishable from the pre-bus build:
    no socket, no context, no syscalls, no counters that could be mistaken for activity.
    """

    enabled = False

    def open(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def publish_ticks(self, ticks) -> bool:  # noqa: ANN001, ARG002
        return False

    def publish_session(self, **_kwargs) -> bool:
        return False

    def status(self) -> dict:
        return {"enabled": False}


def build_publisher(settings) -> BusPublisher | NullBusPublisher:
    """Construct a publisher from settings, or the null object when disabled."""
    if not getattr(settings, "market_data_bus_enabled", False):
        return NullBusPublisher()
    endpoint = getattr(settings, "market_data_bus_ingest_endpoint", "") or ""
    if not endpoint:
        logger.warning(
            "MARKET_DATA_BUS_ENABLED is set but MARKET_DATA_BUS_INGEST_ENDPOINT is empty; "
            "bus publication stays disabled"
        )
        return NullBusPublisher()
    publisher = BusPublisher(
        endpoint,
        sndhwm=int(getattr(settings, "market_data_bus_sndhwm", DEFAULT_SNDHWM)),
        producer=os.environ.get("HOSTNAME", "tickvault"),
    )
    publisher.open()
    return publisher
