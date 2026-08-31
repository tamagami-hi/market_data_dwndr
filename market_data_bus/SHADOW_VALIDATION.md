# MARKET_DATA_BUS — shadow deployment and full-session validation runbook

Operator-executed. Every command below is copy-pasteable on the VPS (`ssh beonedge`).

The bus is a **distribution output only**. Throughout this validation the BIN archive and
the browser WebSocket stay authoritative, and no BOE/algo application may subscribe.

---

## Endpoints (final)

All over the external Docker network `mdbus`, by service DNS name. **No ZMQ port is
published to the VPS host**, so nothing is reachable from the host or the Internet.

| Hop | Endpoint | Socket pair |
|---|---|---|
| TickVault → bus ingest | `tcp://market-data-bus:7301` | PUSH → PULL |
| shadow consumer → bus PUB | `tcp://market-data-bus:7302` | SUB ← PUB |
| future consumers → bus PUB | `tcp://market-data-bus:7302` | SUB ← PUB |
| bus health probe (in-container) | `tcp://127.0.0.1:7303` | REQ → REP |

`tcp://127.0.0.1:7302` is **not** usable from the host and must not be relied on. The
shadow consumer therefore runs as a container on `mdbus` (compose profile `shadow`).

---

## Step 0 — one-time prerequisite (needs root)

`/srv/dev_stack/MARKET_DATA_BUS` currently exists as `root:root`, so the deploy user cannot
write to it:

```
drwxr-xr-x 2 root root 4096 Aug 31 19:36 /srv/dev_stack/MARKET_DATA_BUS
```

```bash
sudo chown -R beonedge:beonedge /srv/dev_stack/MARKET_DATA_BUS
```

I deliberately did not do this via a root Docker container — that would be using the
`docker` group as `sudo` to change ownership under `/srv` on a production host.

### `beonedge` is the correct AND final owner — do not chown this to `10001`

It is worth being precise here, because the TickVault pattern does not carry over.

TickVault needs `10001` on some paths because it **bind-mounts them into the container and
writes to them as uid 10001**. That is visible in the deployed tree:

| Path | Owner | Why |
|---|---|---|
| `/srv/dev_stack/DATA_DOWNLOADER` | `beonedge:beonedge` | deploy dir — the operator edits it |
| `/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA` | `10001:10001` | bind-mounted; the container writes captures here |
| `/srv/data/DATA_DOWNLOADER/ARCHIVE` | `10001:10001` | bind-mounted; the container writes archives here |

MARKET_DATA_BUS has **no equivalent path**, because:

* `compose.yaml` contains **no `volumes:` key at all** — the bus bind-mounts nothing;
* the container root filesystem is `read_only: true`, and its only writable path is an
  in-memory `tmpfs` at `/tmp`;
* the code is baked into the image at build time (`COPY mdbus ./mdbus`), so the running
  container never reads from the host directory;
* transport is TCP over the `mdbus` network, not `ipc://` — there is no socket file whose
  permissions would have to line up across uids. (That was the deciding factor for TCP in
  the first place: the participating containers run as `10001:10001`, `node` and `101:101`.)

So the host directory is only ever touched by **you** and by `docker compose build`. It
needs to be readable/writable by `beonedge` and nothing more. Chowning it to `10001` would
gain nothing and would stop you editing or re-deploying it without `sudo`.

`10001` only becomes relevant if a **later** phase adds a bind-mounted path — a spool
directory, a dump/replay area, or a switch to `ipc://` sockets. If that happens, follow the
DATA_DOWNLOADER pattern: keep the deploy dir `beonedge` and make **only that one
subdirectory** `10001:10001`, e.g.

```bash
# ONLY IF a future phase introduces a bind-mounted path — not needed now
sudo install -d -o 10001 -g 10001 -m 0755 /srv/dev_stack/MARKET_DATA_BUS/run
```

Create the shared network (idempotent):

```bash
docker network create mdbus 2>/dev/null || echo "mdbus already exists"
```

---

## Step 1 — ship the bus

From the dev machine, in the repo root:

```bash
tar czf - --exclude='__pycache__' --exclude='*.pyc' -C market_data_bus . \
  | ssh beonedge 'tar xzf - -C /srv/dev_stack/MARKET_DATA_BUS'
```

Then on the VPS:

```bash
cd /srv/dev_stack/MARKET_DATA_BUS
cp -n .env.example .env       # defaults are fine unless ports 7301-7303 clash
docker compose config --quiet && echo "compose OK"
docker compose up -d --build
docker compose ps             # expect market-data-bus  Up (healthy)
docker compose logs --tail=20 market-data-bus
```

Expect a log line like:

```
market-data bus up: ingest=tcp://0.0.0.0:7301 publish=tcp://0.0.0.0:7302
  health=tcp://0.0.0.0:7303 epoch=<ms> protocol=v1
```

Confirm nothing is exposed to the host:

```bash
docker port market-data-bus            # expect NO output
ss -ltnp | grep -E '7301|7302|7303' || echo "no host listeners — correct"
```

---

## Step 2 — attach TickVault and enable publication

TickVault runs in its own Compose project (`data-downloader`), so it needs the external
network added. In `/srv/dev_stack/DATA_DOWNLOADER/docker-compose.yml`, add to the `backend`
service and at the file's top level:

```yaml
services:
  backend:
    networks:
      - default
      - mdbus

networks:
  mdbus:
    external: true
    name: mdbus
```

In `/srv/dev_stack/DATA_DOWNLOADER/.env`:

```ini
MARKET_DATA_BUS_ENABLED=true
MARKET_DATA_BUS_INGEST_ENDPOINT=tcp://market-data-bus:7301
MARKET_DATA_BUS_SNDHWM=10000
MARKET_DATA_BUS_SESSION_INTERVAL_SECONDS=60
```

Recreate the backend. **Do this while capture is stopped** — outside market hours, after
the EOD sweep completes (15:42 + compression time) and before the next day's 09:10 process
start — so no session is interrupted:

```bash
cd /srv/dev_stack/DATA_DOWNLOADER
docker compose up -d backend
docker compose logs --tail=30 backend | grep -i "market-data bus"
```

Expect:

```
market-data bus publisher connected to tcp://market-data-bus:7301 (SNDHWM=10000, epoch=<ms>)
```

Verify reachability and that the publisher is visible in telemetry:

```bash
docker exec market-data-dwndr-backend python -c \
  "import socket;s=socket.create_connection(('market-data-bus',7301),3);print('ingest reachable');s.close()"

docker exec market-data-dwndr-backend python -c "
import json,urllib.request,os
d=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/api/status',timeout=5))
print(json.dumps(((d.get('monitor') or {}).get('global') or {}).get('transport',{}).get('market_data_bus'),indent=2))"
```

---

## Step 3 — lifecycle independence (run before the session, capture stopped)

Each step must leave TickVault healthy. Check after every one:

```bash
docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' market-data-dwndr-backend
```

`RestartCount` must **not** increase at any point.

```bash
# baseline
docker inspect -f 'restarts={{.RestartCount}}' market-data-dwndr-backend

# 3a. both running                → healthy
# 3b. bus stopped
docker compose -f /srv/dev_stack/MARKET_DATA_BUS/compose.yaml stop market-data-bus
sleep 30 && docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' market-data-dwndr-backend

# 3c. bus restarted
docker compose -f /srv/dev_stack/MARKET_DATA_BUS/compose.yaml start market-data-bus
sleep 20 && docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' market-data-dwndr-backend

# 3d/3e. subscriber stopped / restarted — start and kill the shadow consumer twice
cd /srv/dev_stack/MARKET_DATA_BUS
docker compose --profile shadow run --rm -d shadow-consumer
docker stop market-data-bus-shadow
docker compose --profile shadow run --rm -d shadow-consumer
docker inspect -f '{{.State.Health.Status}} restarts={{.RestartCount}}' market-data-dwndr-backend
```

After the bus outage window, the publisher counters should show drops/errors but capture
must be untouched — re-read `market_data_bus` from `/api/status` as in step 2.

---

## Step 4 — run one complete session in shadow mode

### Which session, exactly

Three different clocks apply to one trading day. Getting them confused is how an earlier
draft of this runbook described the live session as `09:15–15:30`, which is stale for the
2026 exchange structure and would have stopped the shadow run ten minutes before the
derivatives close.

| Clock | Interval | Length | What it is |
|---|---|---|---|
| capture **process envelope** | 09:10 → 15:40 | 6 h 30 m = **23,400 s** | when TickVault runs. Owes no data. |
| equity **derivatives** session | 09:15 → 15:40 | 6 h 25 m = **23,100 s** | continuous index/stock futures + options. The completeness denominator. |
| F&O **cash** continuous session | 09:15 → 15:15 | 6 h 00 m = 21,600 s | the cash underlyings' continuous matching |
| F&O **cash** closing auction (CAS) | 15:15 → 15:35 | 20 m = 1,200 s | captured, but not owed per-second |

So `23400` is the **process/capture envelope**, not a session length. Do not describe the
run as a "6.25-hour session": the continuous derivative trading interval is 6 h 25 m, and
the 23,400 s envelope is 6.5 h of process uptime.

Start the consumer before the open and let it exit on its own after the derivatives close.
`23400` seconds started at 09:10 reaches 15:40 exactly; start it a little early and the
tail covers the close comfortably.

```bash
cd /srv/dev_stack/MARKET_DATA_BUS
docker compose --profile shadow run --rm -d \
  --name market-data-bus-shadow \
  shadow-consumer python -m mdbus.shadow_consumer tcp://market-data-bus:7302 300 23400

docker logs -f market-data-bus-shadow      # 5-minute progress lines
```

**The run must stay active through 15:40.** Stopping at 15:30 leaves the most
session-sensitive ten minutes of the day unmeasured.

The consumer prints a `=== shadow session summary ===` block on exit and writes
`/tmp/shadow-summary.json` inside its container. Capture it **before** the container is
removed: `--rm` deletes the container and its log the moment the process exits, so either
scrape the log first or drop `--rm`.

### Step 4b — the 15:15→15:40 window (report this separately)

This is where the corrected session model earns its keep, and the one interval where
"structurally quiet cash market" must be distinguishable from "F&O feed loss". Sample it at
least at 15:14, 15:16, 15:25, 15:31 and 15:39:

```bash
docker exec market-data-dwndr-backend python -c "
import json,urllib.request,os
d=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/api/status',timeout=5))
g=((d.get('monitor') or {}).get('global') or {})
print(json.dumps({
  'market_phase': g.get('market_phase'),
  'capture_expected': g.get('capture_expected'),
  'sessions': g.get('sessions'),          # per-segment phase: cash AUCTION vs deriv OPEN
  'leg_ages_ms': g.get('leg_ages_ms'),    # cash leg quiet, futures leg live
  'feed_health': g.get('feed_health'),
  'stale_artifacts': g.get('stale_artifacts'),
  'frames_written': g.get('frames_written'),
  'stale_seconds': g.get('stale_seconds'),
  'bus': (g.get('transport') or {}).get('market_data_bus'),
}, indent=2))"
```

Record, for that 25-minute interval only:

| Item | Expected on a healthy day |
|---|---|
| cash continuous state | `CLOSED`/not armed after 15:15 — the continuous session ended |
| cash CAS state | `AUCTION` from 15:15 to 15:35, `capture_expected: true`, `stale_armed: false` |
| index/spot state | `AUCTION` from 15:15 (default `INDEX_SPOT_SESSION=equity_cash`) — **measure whether index values actually keep updating**; if they tick continuously to 15:40, set `INDEX_SPOT_SESSION=equity_deriv` |
| derivative state | `OPEN` throughout, `stale_armed: true`, right up to 15:39:59 |
| stale-artifact reports | no report for `STOCKS:cash`, `NIFTY:spot` or `INDIA_VIX`; any report for a `:options`/`:futures` leg is a real fault |
| frames written by domain | index files, `STOCKS` and `INDICES_FnO` all still advancing at ~1 Hz through 15:39 |
| bus ticks | still flowing; the bus publishes accepted ticks regardless of whether a tick lands in a BIN frame |
| sequence gaps | none |

A quiet cash leg **must not** appear as stale, and live futures data **must not** be
suppressed. If `frames_written` stops advancing before 15:40, that is real derivative loss.

### Collect the four stat sets at EOD

Note EOD now runs at **15:42**, not 15:40: `EOD_TAIL_SECONDS=120` is a deliberate drain
allowance so the final derivative frames are flushed before compression removes the raw
`.bin`. Between 15:40 and 15:42 `/api/status` reports the automation phase as `draining`.

```bash
# 1. TickVault capture + bus publisher
docker exec market-data-dwndr-backend python -c "
import json,urllib.request,os
d=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ['HTTP_PORT']+'/api/status',timeout=5))
g=((d.get('monitor') or {}).get('global') or {})
print(json.dumps({'capture':{k:g.get(k) for k in
  ('captures','ticks_received','unmatched','malformed_ticks','frames_written',
   'frames_expected','stale_seconds','missing_seconds','grid_seconds_lost',
   'write_failures','downtime_seconds','restart_downtime_seconds','escalations')},
 'bus':g.get('transport',{}).get('market_data_bus')},indent=2))"

# 2. MARKET_DATA_BUS
docker exec market-data-bus python -m mdbus.healthcheck
docker exec market-data-bus python -c "
import json,zmq
c=zmq.Context();s=c.socket(zmq.REQ);s.setsockopt(zmq.RCVTIMEO,3000)
s.connect('tcp://127.0.0.1:7303');s.send(b'health')
print(json.dumps(json.loads(s.recv()),indent=2))"

# 3. shadow consumer
docker logs market-data-bus-shadow | sed -n '/shadow session summary/,$p'

# 4. resource impact (run a few times during the session)
docker stats --no-stream --format \
  'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}' \
  market-data-dwndr-backend market-data-bus market-data-bus-shadow
```

---

## Step 5 — capture equivalence at EOD

Compare against a pre-bus session (2026-08-31 is a clean baseline: 22,608 frames,
11,826,853 ticks, 2,018 tokens).

```bash
docker exec market-data-dwndr-backend python -c "
import json,glob
for f in sorted(glob.glob('/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA/_state/stats/capture-*.json'))[-2:]:
    d=json.load(open(f)); g=d.get('global',d)
    print(f.split('/')[-1], {k:g.get(k) for k in
      ('captures','ticks_received','malformed_ticks','stale_seconds',
       'missing_seconds','write_failures','escalations')})"

# archive reconciliation for the session (runs at EOD before compression)
docker exec market-data-dwndr-backend python -c "
import json,glob
p='/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA/_state/stats/reconciliation-history.jsonl'
print(open(p).read().strip().split(chr(10))[-1])"
```

Pass criteria: `missing_seconds`, `stale_seconds`, `write_failures` and `escalations` no
worse than the no-bus baseline, and reconciliation loss unchanged.

---

## Step 6 — what to check in the numbers

| Metric | Where | Expected |
|---|---|---|
| bus uptime | `docker inspect market-data-bus` | no restarts during the session |
| accepted vs published ticks | TickVault `ticks_received` vs `bus_ticks_published` | published ≤ accepted; difference explained by `bus_queue_dropped` |
| publisher queue drops | `bus_queue_dropped` | 0 at ~523 t/s; any non-zero means the worker fell behind |
| ZMQ/HWM send drops | `bus_publish_dropped` | 0; non-zero means the bus or network stalled |
| bus sequence gaps | bus `producer_gaps_total` | 0 (PUSH/PULL is not lossy unless HWM hit) |
| consumer sequence gaps | shadow `sequence_gaps` | 0 for a keeping-up subscriber; PUB/SUB is lossy by design |
| epoch changes | shadow `epoch_changes` | 1 at start; more only if the bus or TickVault restarted |
| unknown tokens | shadow `unknown_tokens` | **must be 0** once the session map has arrived |
| protocol errors | bus + shadow | 0 |
| late-join delay | shadow `first_session_delay_s` | ≤ 60 s (the republish interval) |

`bus_queue_dropped` (producer-side bounded queue full) and `bus_publish_dropped`
(ZMQ `EAGAIN` at the socket HWM) are **separate failure points** and are counted separately
on purpose.

### Session-model checks (independent of the bus)

The corrected session model ships in the same deployment, so validate it in the same run.
None of these involve the bus: TickVault owns market/session interpretation, and ZMQ
availability is never part of session health.

| Check | Where | Expected |
|---|---|---|
| derivatives stay open to 15:40 | `sessions.equity_deriv.phase` | `OPEN` at 15:39:59, `CLOSED` at 15:40 |
| cash leaves continuous matching at 15:15 | `sessions.equity_cash.phase` | `OPEN` → `AUCTION` at 15:15 |
| auction is captured but not armed | `sessions.equity_cash` | `capture_expected: true`, `stale_armed: false` 15:15–15:35 |
| cash closed after the auction | `sessions.equity_cash.phase` | `CLOSED` from 15:35 |
| quiet cash leg is not a fault | `stale_artifacts`, `leg_ages_ms` | no `STOCKS:cash` / `:spot` / `INDIA_VIX` report after 15:15 |
| derivative legs still monitored | `stale_artifacts` | a stalled `:futures`/`:options` leg IS reported, at any time before 15:40 |
| frames keep flowing to 15:40 | `frames_written` | still advancing ~1 Hz at 15:39 |
| no EOD before the close | `automation.phase` | `capture_window` until 15:40, then `draining`, then `eod` at 15:42 |
| cash completeness denominator | EOD reconciliation record | cash-session artifacts show `scheduled_seconds` 21,600 — never 23,100 |
| derivative completeness denominator | EOD reconciliation record | 23,100 owed seconds; a 15:30–15:40 hole appears as real loss |
| auction coverage is reported, not scored | EOD reconciliation record | `auction_seconds: 1200`, `auction_frames` > 0, excluded from loss |

If index spot values are observed still updating continuously between 15:15 and 15:40, set
`INDEX_SPOT_SESSION=equity_deriv` and re-check — that is the one assignment deliberately
left to measurement rather than assumption.

---

## Step 7 — rollback

The bus is additive; rollback is just turning it off.

```bash
# TickVault: set MARKET_DATA_BUS_ENABLED=false in .env, then
cd /srv/dev_stack/DATA_DOWNLOADER && docker compose up -d backend

# bus down (optional)
cd /srv/dev_stack/MARKET_DATA_BUS && docker compose down
```

With `MARKET_DATA_BUS_ENABLED=false` TickVault constructs a `NullBusPublisher`: no socket,
no context, no counters — behaviour identical to the pre-bus build.

---

## Known gap to decide after this run

The session map is only pushed on a 60 s timer, so a late-joining subscriber waits up to
60 s before it can interpret any token. That is acceptable for shadow validation but is a
poor contract for a real consumer, which would start up blind. **Recommendation:** before
the first in-house consumer depends on the bus, add an on-demand request (a REQ/REP
`get_session` on the existing health socket, or a `LAST_VALUE`-style cache the bus replays
to each new subscriber). The bus already retains the most recent session payload in
`_last_session`, so this is a small addition — but it is out of scope for this phase and
must not be built during the shadow run.
