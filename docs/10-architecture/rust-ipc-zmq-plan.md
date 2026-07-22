---
title: Rust Algo Engine IPC (ZeroMQ)
area: architecture
type: plan
status: proposed
tags: [area/architecture, type/plan, status/proposed, ipc, rust]
up: "[[Architecture-MOC]]"
---

# Rust Algo Engine IPC (ZeroMQ Plan)

## The Goal
The current system uses Python to handle the high-I/O WebSocket ingest, 1 Hz snapshotting, and disk persistence. To execute trading strategies (Greek-based, expiry-based) with absolute minimal latency and maximum memory safety, order execution and strategy evaluation will be handled by a separate **Rust Algo Engine**.

This document outlines the planned Inter-Process Communication (IPC) architecture to bridge the Python data downloader and the Rust execution engine.

## The Problem: Python -> Rust Communication
The Rust engine needs real-time access to the market data captured by Python. 
While the Python backend currently broadcasts over WebSockets (used by the Next.js frontend), WebSockets incur minor HTTP framing overhead and TCP loopback latency. 

## The Solution: ZeroMQ (ZMQ) over Unix Domain Sockets
To achieve sub-millisecond (often < 50 microseconds) latency, we will embed **ZeroMQ** in both the Python and Rust services using a brokerless `PUB/SUB` pattern over a **Unix Domain Socket (UDS)**.

### Why ZeroMQ over UDS?
1. **Absolute Lowest Latency:** UDS bypasses the entire network stack. Data moves directly through the OS kernel memory.
2. **Brokerless:** Unlike Redis or Kafka, ZMQ does not require a standalone server container. The library is embedded directly in our Python and Rust binaries, keeping the deployment footprint small.
3. **Decoupled Architecture:** Using the `PUB/SUB` pattern, the Python backend (`PUB`) simply blindly broadcasts ticks. If the Rust engine (`SUB`) crashes or is restarted for an update, Python is unaffected and won't block.

## Implementation Plan

### 1. Python Backend (`PUB`)
- **Dependency:** Add `pyzmq` to the backend dependencies.
- **Integration Point:** Tap into the tick processing pipeline (likely inside or alongside `broadcaster.py` or `engine.py`).
- **Logic:** 
  - Bind a ZMQ `PUB` socket to a file path, e.g., `ipc:///tmp/market_data_stream.sock`.
  - When ticks arrive, serialize them and `send()` them over the socket.
- **Serialization:** To maintain speed, avoid JSON. Use `msgpack` or directly send the raw bytes matching our existing binary (`.bin`) format structure.

### 2. Rust Algo Engine (`SUB`)
- **Dependencies:** Add `zeromq` (or `tmq` for async Tokio integration) and a deserializer (e.g., `rmp-serde` for MessagePack).
- **Logic:**
  - Connect a ZMQ `SUB` socket to `ipc:///tmp/market_data_stream.sock`.
  - Subscribe to all topics (or specific instrument tokens if routing is needed).
  - Deserialize the incoming byte stream into native Rust structs.
  - Pass the structs to the strategy evaluators (Black-Scholes models, Greek calculators).

### 3. Docker Compose Updates
To allow IPC communication between two different Docker containers, they must share the socket file.
- We will mount a shared Docker volume (e.g., `/tmp/ipc_sockets`) into both the Python backend container and the Rust engine container.
- Both services will bind/connect to the `.sock` file located inside this shared volume.

## Latency Perspective
In this architecture, the IPC latency (Python -> Rust) is effectively zero compared to the WAN latency. The time spent passing data over ZMQ will be a fraction of a millisecond. The only real latency bottleneck will be the REST API network trip from the Rust engine to the Zerodha Kite servers for order placement (~50ms - 100ms).
