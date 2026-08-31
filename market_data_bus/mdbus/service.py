"""MARKET_DATA_BUS fan-out service: PULL ingest -> PUB distribution.

Scope is deliberately narrow. This process receives normalized tick batches from TickVault,
stamps its own ordering metadata, and republishes them to in-house subscribers. It does not
maintain option-chain tables, build BIN frames, write market data to disk, compute Greeks,
hold Kite credentials, touch TickVault session state, or contain any strategy logic. It is
distribution infrastructure.

Two ordering domains meet here and must not be confused:

* the **producer** epoch/sequence, as sent by TickVault, which lets the bus see that its
  upstream restarted or that a batch went missing on the PUSH hop;
* the **bus** epoch/sequence, stamped on every outbound message, which is what subscribers
  track. The bus epoch changes when *this* process restarts.

A subscriber therefore detects an upstream restart and a bus restart independently.

Delivery is PUB/SUB, i.e. intentionally lossy: a slow subscriber loses messages and cannot
apply backpressure. Nothing here pretends otherwise, and no metric claims a subscriber
received anything — ZMQ PUB cannot prove that. Consumers needing guaranteed history use
TickVault's BIN archive.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field

import zmq

from mdbus.protocol import (
    MSG_TICK_BATCH,
    PROTOCOL_VERSION,
    TOPIC_SESSION,
    TOPIC_TICK,
    BusHeader,
    ProtocolError,
    decode_header,
    encode_header,
)

logger = logging.getLogger("mdbus")


@dataclass
class BusConfig:
    ingest_endpoint: str = "tcp://0.0.0.0:7301"
    publish_endpoint: str = "tcp://0.0.0.0:7302"
    health_endpoint: str = "tcp://0.0.0.0:7303"
    rcvhwm: int = 50_000
    sndhwm: int = 50_000
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env=None) -> BusConfig:
        env = os.environ if env is None else env
        return cls(
            ingest_endpoint=env.get("MDBUS_INGEST_ENDPOINT", cls.ingest_endpoint),
            publish_endpoint=env.get("MDBUS_PUBLISH_ENDPOINT", cls.publish_endpoint),
            health_endpoint=env.get("MDBUS_HEALTH_ENDPOINT", cls.health_endpoint),
            rcvhwm=int(env.get("MDBUS_RCVHWM", cls.rcvhwm)),
            sndhwm=int(env.get("MDBUS_SNDHWM", cls.sndhwm)),
            log_level=env.get("MDBUS_LOG_LEVEL", cls.log_level),
        )


@dataclass
class BusMetrics:
    """Counters the bus can measure honestly. No invented delivery guarantees."""

    started_ms: int = 0
    bus_epoch: int = 0
    messages_in_total: int = 0
    messages_out_total: int = 0
    ticks_in_total: int = 0
    ticks_out_total: int = 0
    session_messages_total: int = 0
    publish_dropped_total: int = 0
    protocol_errors_total: int = 0
    producer_epochs_seen: int = 0
    producer_gaps_total: int = 0
    producer_missed_total: int = 0
    last_message_ms: int | None = None
    last_session_ms: int | None = None
    _rate_window: list[tuple[int, int]] = field(default_factory=list)

    def snapshot(self, now_ms: int) -> dict:
        window = [(t, n) for t, n in self._rate_window if now_ms - t <= 5_000]
        span_ms = max(1, now_ms - window[0][0]) if window else 0
        ticks = sum(n for _, n in window)
        messages = len(window)
        uptime_ms = max(0, now_ms - self.started_ms)
        return {
            "bus_up": True,
            "protocol_version": PROTOCOL_VERSION,
            "current_bus_epoch": self.bus_epoch,
            "uptime_ms": uptime_ms,
            "messages_in_total": self.messages_in_total,
            "messages_out_total": self.messages_out_total,
            "ticks_in_total": self.ticks_in_total,
            "ticks_out_total": self.ticks_out_total,
            "session_messages_total": self.session_messages_total,
            "publish_dropped_total": self.publish_dropped_total,
            "protocol_errors_total": self.protocol_errors_total,
            "producer_epochs_seen": self.producer_epochs_seen,
            "producer_gaps_total": self.producer_gaps_total,
            "producer_missed_total": self.producer_missed_total,
            "last_message_age_ms": (
                None if self.last_message_ms is None else max(0, now_ms - self.last_message_ms)
            ),
            "last_session_age_ms": (
                None if self.last_session_ms is None else max(0, now_ms - self.last_session_ms)
            ),
            "messages_per_second": round(messages / (span_ms / 1000.0), 2) if span_ms else 0.0,
            "ticks_per_second": round(ticks / (span_ms / 1000.0), 2) if span_ms else 0.0,
        }

    def record(self, now_ms: int, ticks: int) -> None:
        self._rate_window.append((now_ms, ticks))
        if len(self._rate_window) > 20_000:
            del self._rate_window[:10_000]


class MarketDataBus:
    """PULL ingest, PUB fan-out, and a REP health socket, driven by one poll loop."""

    def __init__(self, config: BusConfig, *, clock=None, context=None) -> None:
        self.config = config
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._context = context or zmq.Context()
        self._owns_context = context is None
        self.metrics = BusMetrics()
        self.metrics.started_ms = self._clock()
        self.metrics.bus_epoch = self.metrics.started_ms
        self.sequence = 0
        self._producer_epoch: int | None = None
        self._producer_sequence = 0
        self._last_session: tuple[bytes, bytes] | None = None
        self._stop = threading.Event()
        self.ingest: zmq.Socket | None = None
        self.publish: zmq.Socket | None = None
        self.health: zmq.Socket | None = None

    def open(self) -> None:
        self.ingest = self._context.socket(zmq.PULL)
        self.ingest.setsockopt(zmq.RCVHWM, self.config.rcvhwm)
        self.ingest.setsockopt(zmq.LINGER, 0)
        self.ingest.bind(self.config.ingest_endpoint)

        self.publish = self._context.socket(zmq.PUB)
        self.publish.setsockopt(zmq.SNDHWM, self.config.sndhwm)
        self.publish.setsockopt(zmq.LINGER, 0)
        self.publish.bind(self.config.publish_endpoint)

        self.health = self._context.socket(zmq.REP)
        self.health.setsockopt(zmq.LINGER, 0)
        self.health.bind(self.config.health_endpoint)

        logger.info(
            "market-data bus up: ingest=%s publish=%s health=%s epoch=%d protocol=v%d",
            self.config.ingest_endpoint,
            self.config.publish_endpoint,
            self.config.health_endpoint,
            self.metrics.bus_epoch,
            PROTOCOL_VERSION,
        )

    def close(self) -> None:
        for socket in (self.ingest, self.publish, self.health):
            if socket is not None:
                socket.close(linger=0)
        self.ingest = self.publish = self.health = None
        if self._owns_context:
            self._context.term()

    def request_stop(self) -> None:
        self._stop.set()

    def handle_ingest(self, parts: list[bytes]) -> bool:
        """Validate one inbound multipart message and republish it. Returns True if sent."""
        now = self._clock()
        if len(parts) != 3:
            self.metrics.protocol_errors_total += 1
            logger.warning("dropping malformed ingest message with %d part(s)", len(parts))
            return False
        topic, raw_header, payload = parts
        try:
            header = decode_header(raw_header)
        except ProtocolError as exc:
            self.metrics.protocol_errors_total += 1
            logger.warning("dropping message with unusable header: %s", exc)
            return False
        if topic not in (TOPIC_TICK, TOPIC_SESSION):
            self.metrics.protocol_errors_total += 1
            logger.warning("dropping message on unknown topic %r", topic)
            return False
        self._track_producer(header)
        self.metrics.messages_in_total += 1
        self.metrics.last_message_ms = now
        if header.message_type == MSG_TICK_BATCH:
            self.metrics.ticks_in_total += header.count
        else:
            self.metrics.session_messages_total += 1
            self.metrics.last_session_ms = now
            self._last_session = (raw_header, payload)

        return self._republish(topic, header, payload, now)

    def _track_producer(self, header: BusHeader) -> None:
        if self._producer_epoch is None or header.epoch != self._producer_epoch:
            if self._producer_epoch is not None:
                logger.info(
                    "producer epoch changed %d -> %d (upstream restarted)",
                    self._producer_epoch,
                    header.epoch,
                )
            self.metrics.producer_epochs_seen += 1
            self._producer_epoch = header.epoch
            self._producer_sequence = header.sequence
            return
        if header.sequence > self._producer_sequence + 1:
            missed = header.sequence - self._producer_sequence - 1
            self.metrics.producer_gaps_total += 1
            self.metrics.producer_missed_total += missed
        if header.sequence > self._producer_sequence:
            self._producer_sequence = header.sequence

    def _republish(self, topic: bytes, header: BusHeader, payload: bytes, now: int) -> bool:
        """Stamp bus ordering metadata and PUB the message.

        The outbound header keeps the producer's ``message_type`` and ``count`` but carries
        the **bus** epoch and sequence, so a subscriber's gap detection is against the bus
        and is unaffected by how the producer batched its sends.
        """
        if self.publish is None:
            return False
        self.sequence += 1
        out_header = encode_header(
            BusHeader(
                epoch=self.metrics.bus_epoch,
                sequence=self.sequence,
                publish_unix_ns=time.time_ns(),
                message_type=header.message_type,
                count=header.count,
            )
        )
        try:
            self.publish.send_multipart([topic, out_header, payload], flags=zmq.DONTWAIT)
        except zmq.Again:
            self.metrics.publish_dropped_total += 1
            self.sequence -= 1
            return False
        self.metrics.messages_out_total += 1
        if header.message_type == MSG_TICK_BATCH:
            self.metrics.ticks_out_total += header.count
            self.metrics.record(now, header.count)
        return True

    def handle_health(self) -> None:
        """Answer a health probe with the honest metric snapshot (no credentials)."""
        if self.health is None:
            return
        try:
            self.health.recv(flags=zmq.DONTWAIT)
        except zmq.Again:
            return
        payload = json.dumps(self.metrics.snapshot(self._clock())).encode("utf-8")
        try:
            self.health.send(payload, flags=zmq.DONTWAIT)
        except zmq.Again:
            logger.debug("health reply could not be queued")

    def run(self) -> None:  # pragma: no cover - live loop, exercised by integration tests
        poller = zmq.Poller()
        poller.register(self.ingest, zmq.POLLIN)
        poller.register(self.health, zmq.POLLIN)
        while not self._stop.is_set():
            try:
                events = dict(poller.poll(timeout=250))
            except zmq.ContextTerminated:
                break
            except KeyboardInterrupt:
                break
            if self.ingest in events:
                for _ in range(1024):
                    try:
                        parts = self.ingest.recv_multipart(flags=zmq.DONTWAIT)
                    except zmq.Again:
                        break
                    self.handle_ingest(parts)
            if self.health in events:
                self.handle_health()
        logger.info("market-data bus stopping")


def main() -> int:  # pragma: no cover - process entrypoint
    config = BusConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bus = MarketDataBus(config)
    bus.open()

    def _stop(signum, _frame) -> None:
        logger.info("received signal %s", signum)
        bus.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)
    try:
        bus.run()
    finally:
        bus.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
