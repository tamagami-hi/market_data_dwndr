# Market Data Downloader - API Status Commands

This document contains a comprehensive list of `curl` commands you can use to view the live status of the deployed `market_data_dwndr` backend.

Assuming you are running these commands from the VPS or a local machine with port 9000 exposed, the base URL is `http://localhost:9000`.

*Note: For cleaner output, it is highly recommended to pipe the responses into `jq` as shown below.*

---

## 1. Global System Status (The Master Endpoint)
Returns the complete state of the engine, including authentication, the daily automation schedule, the active capture configuration, and real-time ingestion metrics.

```bash
curl -s http://localhost:9000/api/status | jq
```

---

## 2. Authentication Status
Returns only the current broker session state, token availability, and risk-free rate data.

```bash
curl -s http://localhost:9000/api/auth/status | jq
```

---

## 3. Capture & Downloader Status
Returns the state of the capture engine (running vs stopped) and the exact list of configured indices, stocks, and total tokens being monitored.

```bash
curl -s http://localhost:9000/api/capture/status | jq
```

---

## 4. Capture History & Files
Returns a list of all historical tick files (e.g., `_live.bin`, `_historical.bin`) currently stored on the disk for the current and past trading days.

```bash
curl -s http://localhost:9000/api/capture/history | jq
```

---

## 5. Stock Depth (Live Data)
Returns the real-time order book (depth) for a specific stock symbol if the capture engine is currently running and monitoring that stock.

```bash
# Replace 'RELIANCE' with any valid NSE stock symbol
curl -s http://localhost:9000/api/capture/stocks/RELIANCE/depth | jq
```

---

## 6. System Healthcheck
A fast, lightweight endpoint used by Docker to verify if the server is responsive. Returns `{"status": "ok"}`.

```bash
curl -s http://localhost:9000/health | jq
```

---

## 7. Saved Frames & Ingestion Metrics
To specifically check how many data frames have been written to disk (either globally or per index), you can use the master status endpoint and filter it with `jq`.

**Total Global Frames Captured:**
```bash
curl -s http://localhost:9000/api/status | jq '.monitor.global.captures'
```

**Frames Written Per Index / Stock Group:**
```bash
curl -s http://localhost:9000/api/status | jq '.monitor.per_underlying[] | {underlying, frames_written, file_bytes}'
```

---

## 8. Advanced Diagnostics & Troubleshooting Filters

Use these advanced `jq` filters to quickly query the health and performance of your system.

**Check for Disconnected Streams or Missing Heartbeats:**
Returns only the names of the streams that are disconnected or failing. If everything is healthy, this returns nothing.
```bash
curl -s http://localhost:9000/api/status | jq '.monitor.per_underlying[] | select(.connected == false or .heartbeat_ok == false) | .underlying'
```

**Calculate Live Ingestion Latency (in milliseconds):**
Calculates the exact delay between the last received tick and the current system time.
```bash
curl -s http://localhost:9000/api/status | jq '{ latency_ms: (.generated_at - .automation.last_tick_at) }'
```

**Check for Data Loss or Pipeline Degradation:**
```bash
curl -s http://localhost:9000/api/status | jq '{ dropped_batches: .monitor.global.dropped_batches, ingestion_degraded: .monitor.global.ingestion_degraded }'
```

**View Disk Usage in Megabytes (MB):**
```bash
curl -s http://localhost:9000/api/status | jq '{ disk_usage_MB: (.monitor.global.disk_bytes / 1048576) }'
```

**Get a 4-Line "Green Light" Summary Health Check:**
Quickly see if you are authenticated, running, the current market phase, and if the data stream is 100% healthy.
```bash
curl -s http://localhost:9000/api/status | jq '{ auth: .session.authenticated, capture: .capture.running, market_phase: .session.market_phase, healthy: (.monitor.global.dropped_batches == 0 and .monitor.global.ingestion_degraded == false) }'
```
