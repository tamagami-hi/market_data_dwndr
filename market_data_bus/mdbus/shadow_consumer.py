"""Diagnostic SUB consumer for shadow-mode validation.

Run this alongside a live TickVault session before any application depends on the bus. It
subscribes to everything, tracks ordering with (epoch, sequence), and prints a periodic
summary so the rollout can be judged on evidence: tick counts, gaps, epoch changes,
publish latency, and whether session metadata actually arrived.

It deliberately does not persist anything. TickVault's BIN archive stays the authoritative
historical record during validation; this only answers "is the stream healthy".

**Run it as a container on the ``mdbus`` network**, not from the VPS host. No ZMQ port is
published to the host, so ``tcp://127.0.0.1:7302`` would only work if someone had exposed
one — which would weaken the perimeter. The default endpoint below is the Docker DNS name
of the bus service, reachable only by containers attached to ``mdbus``::

    docker compose --profile shadow run --rm shadow-consumer

Optional args: ``<publish-endpoint> <report-interval-s> <run-seconds>``. With
``run-seconds`` set it exits on its own and prints the summary, which is what an unattended
full-session run wants.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

import zmq

from mdbus.protocol import (
    MSG_SESSION,
    MSG_TICK_BATCH,
    TOPIC_SESSION,
    TOPIC_TICK,
    ProtocolError,
    SequenceTracker,
    decode_header,
    decode_session,
    decode_tick_batch,
)

DEFAULT_ENDPOINT = "tcp://market-data-bus:7302"


def main() -> int:
    endpoint = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("MDBUS_SUB_ENDPOINT", DEFAULT_ENDPOINT)
    )
    report_every_s = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    run_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    summary_path = os.environ.get("MDBUS_SHADOW_SUMMARY")

    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.setsockopt(zmq.RCVHWM, 200_000)
    sub.connect(endpoint)

    tracker = SequenceTracker()
    ticks = messages = sessions = protocol_errors = 0
    instruments: dict[int, dict] = {}
    latencies: list[float] = []
    tokens_seen: set[int] = set()
    first_session_at: float | None = None
    started = time.monotonic()
    next_report = started + report_every_s
    deadline = started + run_seconds if run_seconds > 0 else None

    print(
        f"shadow consumer on {endpoint} "
        f"(report every {report_every_s:g}s"
        + (f", exiting after {run_seconds:g}s)" if deadline else ", Ctrl-C to stop)"),
        flush=True,
    )
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                parts = sub.recv_multipart(flags=zmq.DONTWAIT)
            except zmq.Again:
                time.sleep(0.005)
                parts = None
            if parts is not None:
                try:
                    if len(parts) != 3:
                        raise ProtocolError(f"expected 3 parts, got {len(parts)}")
                    topic, raw_header, payload = parts
                    header = decode_header(raw_header)
                    tracker.observe(header)
                    messages += 1
                    latencies.append((time.time_ns() - header.publish_unix_ns) / 1e6)
                    if header.message_type == MSG_TICK_BATCH and topic == TOPIC_TICK:
                        batch = decode_tick_batch(payload, header.count)
                        ticks += len(batch)
                        tokens_seen.update(t.instrument_token for t in batch)
                    elif header.message_type == MSG_SESSION and topic == TOPIC_SESSION:
                        sessions += 1
                        if first_session_at is None:
                            first_session_at = time.monotonic() - started
                            print(
                                f"  first market.session after {first_session_at:.2f}s",
                                flush=True,
                            )
                        session = decode_session(payload)
                        instruments = {
                            i["instrument_token"]: i for i in session["instruments"]
                        }
                except ProtocolError as exc:
                    protocol_errors += 1
                    print(f"  protocol error: {exc}", flush=True)

            now = time.monotonic()
            if now >= next_report:
                elapsed = now - started
                lat = sorted(latencies[-5000:])
                unmapped = len(tokens_seen - set(instruments))
                line = (
                    f"[{elapsed:7.1f}s] msgs={messages:<8} ticks={ticks:<10} "
                    f"{ticks / elapsed:8.1f} t/s  gaps={tracker.gaps} "
                    f"missed={tracker.missed} epochs={tracker.epoch_changes} "
                    f"sessions={sessions} instruments={len(instruments)} "
                    f"unmapped_tokens={unmapped} proto_err={protocol_errors}"
                )
                if lat:
                    line += f" lat_p50={statistics.median(lat):.2f}ms"
                print(line, flush=True)
                next_report = now + report_every_s
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = max(1e-9, time.monotonic() - started)
        lat = sorted(latencies)
        summary = {
            "endpoint": endpoint,
            "duration_s": round(elapsed, 1),
            "messages_received": messages,
            "ticks_received": ticks,
            "ticks_per_second": round(ticks / elapsed, 2),
            "session_messages": sessions,
            "first_session_delay_s": (
                None if first_session_at is None else round(first_session_at, 2)
            ),
            "bus_epoch": tracker.epoch,
            "sequence_gaps": tracker.gaps,
            "messages_missed": tracker.missed,
            "epoch_changes": tracker.epoch_changes,
            "protocol_errors": protocol_errors,
            "distinct_tokens_seen": len(tokens_seen),
            "instruments_mapped": len(instruments),
            "unknown_tokens": len(tokens_seen - set(instruments)),
            "latency_p50_ms": round(statistics.median(lat), 3) if lat else None,
            "latency_p99_ms": (
                round(lat[int(len(lat) * 0.99)], 3) if len(lat) > 100 else None
            ),
            "latency_max_ms": round(lat[-1], 3) if lat else None,
        }
        print()
        print("=== shadow session summary ===")
        for key, value in summary.items():
            print(f"{key:<24} {value}")
        if summary_path:
            try:
                with open(summary_path, "w") as handle:
                    json.dump(summary, handle, indent=2)
                print(f"\nsummary written to {summary_path}")
            except OSError as exc:
                print(f"\ncould not write summary: {exc}")
        sub.close(linger=0)
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
