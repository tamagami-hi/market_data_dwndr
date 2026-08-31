"""Container healthcheck: ask the bus's own REP socket for its metric snapshot.

Exits 0 when the bus answers, 1 otherwise. Deliberately does not assert a tick rate: the
bus is healthy when it is up and serving, and outside market hours a zero rate is correct.
Classifying quiet as unhealthy would restart a working service every evening.
"""

from __future__ import annotations

import json
import os
import sys

import zmq


def probe(endpoint: str, timeout_ms: int = 2_000) -> dict | None:
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
    socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
    try:
        socket.connect(endpoint)
        socket.send(b"health")
        return json.loads(socket.recv().decode("utf-8"))
    except Exception:  # noqa: BLE001 - any failure is an unhealthy verdict
        return None
    finally:
        socket.close(linger=0)
        context.term()


def main() -> int:
    endpoint = os.environ.get("MDBUS_HEALTH_PROBE_ENDPOINT") or os.environ.get(
        "MDBUS_HEALTH_ENDPOINT", "tcp://127.0.0.1:7303"
    ).replace("0.0.0.0", "127.0.0.1")
    snapshot = probe(endpoint)
    if snapshot is None or not snapshot.get("bus_up"):
        print(f"market-data bus did not answer on {endpoint}", file=sys.stderr)
        return 1
    print(
        f"bus_up epoch={snapshot.get('current_bus_epoch')} "
        f"in={snapshot.get('messages_in_total')} out={snapshot.get('messages_out_total')} "
        f"dropped={snapshot.get('publish_dropped_total')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
