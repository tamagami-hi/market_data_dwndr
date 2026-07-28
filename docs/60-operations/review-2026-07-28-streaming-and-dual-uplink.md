# Review — 2026-07-28: live streaming, monitor telemetry, dual-uplink network

A single working session that covered two unrelated areas. Both are recorded here because
several conclusions correct earlier assumptions and are easy to re-derive wrongly.

- **Part 1 — application**: stream everything the capture records, and fix three monitor
  metrics that were measuring the wrong thing.
- **Part 2 — infrastructure**: the VPS gained a second ISP, lost its LAN address, and
  along the way revealed that reaching TickVault by IP has been working by accident.

Related: [[lan-and-public-domain-routing]], [[nginx-vhost-guide]], [[config-and-env]].

---

# Part 1 — Application

## 1.1 Full L1–L5 stock streaming (`0648e0e`)

**Problem.** The stocks page received ~9 values per stock while capture recorded **164**
(4 legs × 11 scalars, plus 5 depth levels × 6 fields). Depth reached the UI only through
`GET /api/capture/stocks/{symbol}/depth` on row expand, so the order book was a frozen
snapshot until the row was collapsed and reopened.

**Design decision — columnar payload.** One array per field indexed by stock row, rather
than an object per stock:

```json
"legs": { "fut_current": {
    "scalars": { "ltp": [...], "oi": [...], "volume": [...] },
    "depth": [ { "bid_price": [...], "bid_qty": [...] }, ×5 ]
}}
```

A row-per-stock shape would repeat ~41 JSON keys 210 times *per leg*. Measured cost of the
columnar form for 210 stocks:

| | |
|---|---|
| JSON per frame | 421 KiB |
| **On the wire** (permessage-deflate already active) | **38 KiB** |

Column metadata comes from the BIN schema, so `col.is_price` drives paise→rupees and any
new column streams without touching the serialiser.

**Frontend.** `lib/stockBoard.ts` holds projections (`stockRows`, `legDepth`,
`depthFromBoard`) that extract one stock from the columnar arrays only when rendered.
Expanding a row became pure local state — no fetch, no loading/error branches — so the book
updates every second. `StockDepthPanel` lost its `isLoading`/`error` props.

The REST depth endpoint is retained for `curl`/diagnostics but has no UI callers.

## 1.2 Options are L1-only — by design

Confirmed and deliberately unchanged. `RAW_BLOCK_COLUMNS` has **15 flat columns and no
depth structure**:

```
ltp oi volume buy_quantity sell_quantity
bid bid_qty ask ask_qty                    ← a single level
oi_day_high oi_day_low ohlc_open/high/low/close
```

The write path proves it at ingest: `chain/table.py` calls `best_bid_ask(tick)`, documented
as *"(bid_price, bid_qty, ask_price, ask_qty) in paise/qty from **L1** of the book"*, reading
`depth_side(tick, "buy", 1)[0]`. Kite sends 5 levels in `full` mode; L2–L5 are discarded at
ingest and never reach disk.

`bid_qty` / `ask_qty` **are persisted but not streamed** — the L1 sizes exist in historical
files and are available for reconstruction, just not shown live.

## 1.3 Greeks cost — an earlier estimate was wrong

An initial benchmark reported ~2.8 ms for all four chains. That used synthetic premiums
(`intrinsic + 120` for every strike), which converge in very few Newton iterations.
Re-measured against **real captured frames**:

| Index | Strikes | IVs solved | Greeks |
|---|---|---|---|
| BANKNIFTY | 101 | 176/202 | 4.49 ms |
| FINNIFTY | 101 | 166/202 | 4.51 ms |
| NIFTY | 97 | 175/194 | 4.34 ms |
| SENSEX | 101 | 174/202 | 2.63 ms |
| **All four** | | | **16.0 ms** |

Plus 0.3 ms chain metrics and 2.7 ms for the 210-stock columnar board → **~19 ms of the
1000 ms budget (1.9%)**.

**Conclusion unchanged, number corrected ~6×.** Parallel Greeks remain unnecessary: 19 ms
leaves large headroom, and threads would not help because this is GIL-bound pure-Python
math. Any future benchmark must use real frames — contrived premiums understate the cost.

## 1.4 Three monitor metric fixes

**ticks/sec was a lifetime average** (`728c772`). It computed `ticks_received / uptime`,
which only creeps toward the session mean and — worse — keeps reporting a healthy number
after ingest stops. Now a trailing-window rate using the same sampler as `fps`, measured
against the oldest sample in the window. Test asserts both halves: a steady 100 ticks/s
reads ~100, and a stalled feed decays to **0**.

**Latency now measures the build window** (`728c772`). Previously wall-clock from the grid
timestamp, which conflated queueing delay with actual work. Now a `perf_counter` started
immediately before the first Greeks reconstruction and stopped once the whole 1 Hz batch is
encoded and ready for the hub — broken out in `meta` as `greeks_ms` and `stocks_ms`.
`last_queue_ms` is kept server-side to separate "the build is slow" from "we were queued".

Measured server-side deliberately: comparing a server timestamp to browser `Date.now()`
would report clock skew, not latency.

**LOSS column** now uses elapsed grid seconds rather than the full-day baseline, with a
separate `Day` progress column. Previously a healthy 10:30 session showed an alarming
"75.6% loss".

## 1.5 Stats now survive a mid-session restart (`8e09edd`)

**The data was never at risk.** Writers open `"ab"` and emit a header only when the file is
empty, so a restart continues the same file. Locked in by a test: two writer generations
over one path produce 8 readable frames with a single header.

**The stats were the bug.** Every counter lived in process memory, so `frames_written`
restarted at 0 and `first_capture_ms` at "now". Measured against the live files, a restart
would have reported:

| | Before | After |
|---|---|---|
| Frames | **0** of 17,875 | **17,875** |
| Session start | "now" | true 09:00 IST |
| Session loss | **100%** | **0.24%** |

That 100% was the reported symptom ("previous stats just disappeared").

`resume_from_disk()` seeds day state at bootstrap from two sources by authority:

1. **The `.bin` files** — frame counts and the session's true first timestamp.
   Authoritative, being the data that actually landed.
2. **The last persisted monitor snapshot** — `grid_gaps`, `grid_seconds_lost`,
   `frozen_seconds`, which leave no trace in the files. Best-effort; the final few seconds
   may be missing.

The restart hole is **not** hidden: seconds between the last frame on disk and startup are
counted as one extra gap plus lost seconds.

`frames_written` became a property = `frames_on_disk + frames_appended`, so the monitor
reports the day while the process-local figure stays available.

**New `app/bin_codec/scan.py`** walks only the `[u32 len][payload]` framing instead of using
the mmap readers (which run the full validating decode). Validated against all five live
files — exact match on count and first/last timestamps:

```
BANKNIFTY   410 MB  scan=17538 reader=17538   387 ms
FINNIFTY    410 MB  scan=17539 reader=17539   422 ms
NIFTY       393 MB  scan=17539 reader=17539   381 ms
SENSEX      410 MB  scan=17540 reader=17540   419 ms
STOCKS     4070 MB  scan=17540 reader=17540   476 ms
```

Under half a second, paid once at startup, and it tolerates a torn trailing frame.

## 1.6 Monitor UI fixes

**Duplicate latency** (`abcfc09`). The monitor renders two dots (`capture-status` and
`session`). Build latency is one server-wide measurement stamped on every message in the
batch, so both dots necessarily showed the identical number — reading as two independent
latencies. Now opt-out per dot via `showLatency`, disabled on the session heartbeat dot.
Throughput stays on both because bytes/sec genuinely *is* per-topic.

**Redundant red box** (`89e363b`). The banner under Data loss restated the Seconds lost and
Gap events tiles above it, costing a row of height to say nothing new. Severity moved onto
the fields: `Stat` takes a tone, so lost seconds and gaps turn red, frozen amber, elapsed
loss amber above 0 and red at 1%+. Tooltips preserve the distinction that frozen seconds
still have frames written (duplicate contents) whereas lost seconds do not exist.

**Panel alignment** (`a6abae3`). Six panels sat in two independent flex columns, so each
stacked at its own natural heights and nothing lined up across the gutter. Now one grid with
panels as direct children in row order. Measured at 1600×1000:

```
row 1: Data loss       top=186 h=224  |  Per underlying   top=186 h=224
row 2: Frame integrity top=418 h=224  |  Session history  top=418 h=224
row 3: Download hist.  top=650 h=224  |  Compression      top=650 h=224
```

`Panel` swapped a now-meaningless `flex-1` (parent is no longer a flex column) for
`h-full`. An e2e check asserts each pair shares a top edge and height within 1px — and was
confirmed to **fail** against the previous layout, so it is a real guard.

**Session history expanded** (`702903c`). It had 7 columns in a panel sized for far more.
Now 12, mirroring the Data loss panel's order: `Frames, Loss, Lost s, Gaps, Frozen, Drop,
Unmatch, Ticks/s, Recon, Uptime, Disk`. No backend change was needed — every field was
already recorded and typed on `SessionSummary`; the table simply wasn't reading them.
`Ticks/s` is derived as `ticks_received / uptime` and labelled a session **average**, to
distinguish it from the live trailing-window rate.

## 1.7 Session history — what it is for, and its current limits

A cross-day audit trail of capture quality, one row per trading date in
`session-history.jsonl` (capped at 365 days). Everything else on the monitor page is about
*now* and vanishes when capture stops; this is the only thing that survives. Its practical
use is deciding whether to trust a day before backtesting on it, without re-reading 4 GB of
`.bin` files.

Limits found:

- **Only one row exists.** The feature is recent and has no backfill, so its value is
  prospective. `capture-*.json` snapshots exist for earlier days without matching rows.
- **That row was wrong**: `captures=6629` against 17,875 frames actually on disk, and 0.00%
  loss. It was written when capture stopped at 14:22 by a process started ~12:32 after an
  earlier restart — 6,600 seconds, matching almost exactly. The restart-resume fix addresses
  this; the next clean stop should record day totals.
- **Rows are replaced, not appended**, keyed by trading date. Combined with per-process
  counters, the last restart of the day silently overwrote earlier figures.
- **Only written on a clean stop** (explicit stop or shutdown via `main.py`). A SIGKILL, OOM
  or crash leaves no row, so an absent row means "ended badly", not "no data".

## 1.8 WebSocket stream hygiene — no duplicates

Each topic is a module-level connection with reference counting:

```ts
acquire() { refCount += 1; if (refCount === 1) connect(); }
function connect() { if (ws || refCount === 0) return; }
```

That double guard means a `ConnectionDot` plus envelope handlers on one page produce **one**
socket, not three. Four tests pin it: 3 consumers → 1 socket, the socket survives until the
last release, topics are distinct objects, and an unacquired connection never connects.

Two observations:

- **`historical-jobs` is exported but has no UI consumer** — dead code, though harmless
  since an unacquired connection opens no socket. Still listed in the backend's
  `ALLOWED_TOPICS`.
- **The monitor's two sockets are both legitimate.** `session` initially looked redundant
  against `capture-status`, but it carries `LOG` and `SESSION_STATUS` messages for the log
  strip; its per-second heartbeat serves as the dot's staleness keepalive.

## 1.9 Commits

| Commit | |
|---|---|
| `0648e0e` | full L1–L5 stocks streaming, no REST polling |
| `728c772` | ticks/sec as a real rate; latency = Greeks→batch-ready window |
| `8e09edd` | stats persist across a mid-session restart |
| `abcfc09` | build latency shown once, not on both dots |
| `89e363b` | data-loss severity on fields, red box removed |
| `a6abae3` | dashboard panels aligned into rows |
| `702903c` | session history filled with data-loss stats; WS hygiene tests |

Verification at each step: backend 439 tests + ruff; frontend `tsc`, eslint, 12 unit tests,
production build, 23 e2e render checks.

---

# Part 2 — Infrastructure

## 2.1 AdGuard taken down

Requested because `192.168.29.2` lived on the WiFi card, which was being retired.

It was **already dead before removal**: its DNS ports bind `192.168.29.2`, and at the time
that address was on no interface (`ip route get` → *Network is unreachable*), because the
WiFi DHCP lease had dropped. The NetworkManager journal showed it flapping:

```
16:31:13  dhcp4 (wlx…): no lease
16:35:46  dhcp4 (wlx…): new lease, address=192.168.29.2
16:40:17  dhcp4 (wlx…): no lease
```

Its upstream was broken too — every recent log line was a Quad9 DoH failure
(`unexpected EOF` to `dns10.quad9.net`).

Taken down with `docker compose down`, which preserves `adguard/confdir` and
`adguard/workdir`, so `up -d` restores it. **The capture pipeline was never at risk**: the
VPS resolves via `systemd-resolved` → Tailscale (`100.100.100.100`), not AdGuard.

## 2.2 The wired link had no IPv4 — the important find

After switching to ethernet, the box had **no IPv4 route at all**:

```
enp2s0            169.254.25.108/16      ← link-local, i.e. no DHCP lease
ipv4.method:      link-local             ← NetworkManager never asked for DHCP
ip route default: (IPv6 only, via RA)
```

Consequence, measured:

```
IPv4 to api.kite.trade:  FAILED (HTTP 000)
IPv6 to api.kite.trade:  FAILED (no AAAA record)
```

**No route to the broker API.** Today's session had already finished so its data was intact,
but the next 09:00 session would have failed. SSH kept working only because Tailscale rides
the IPv6 default route.

Resolved by the user setting IPv4 **and** IPv6 to automatic. Confirmed afterwards:

```
api.kite.trade      HTTP 200 in 0.12s
kite.zerodha.com    HTTP 200 in 0.13s
```

### Why "just reset NetworkManager" would not have worked

`/etc/NetworkManager/system-connections/` is **empty** — no profiles live there. They are
all generated by netplan (`00-installer-config.yaml` for `netplan-enp2s0`, plus three
`90-NM-*.yaml` for the WiFi profiles). So a restart re-reads the same `link-local` setting,
and deleting the profile gets it regenerated identically. The setting is declared in a file;
that file has to change. `sudo netplan set ethernets.enp2s0.dhcp4=true` plus
`sudo netplan try` (auto-reverts in 120 s) is the low-risk route.

Also noted: `no-auto-default` is not set and only `fan-*` interfaces are unmanaged, so NM
*would* auto-create a DHCP wired connection if no profile matched.

## 2.3 Current state: two ISPs, dual-homed VPS

```
enp2s0           (Airtel, ethernet)  192.168.1.2/24    static DHCP reservation
wlx1cbfce1488ce  (Jio, WiFi)         192.168.29.2/24   static DHCP reservation
tailscale0                           100.122.85.101/32
MAC enp2s0       1c:1b:0d:12:69:e2
```

Both default routes coexist, split by metric:

```
default via 192.168.1.1   dev enp2s0           metric 100   ← preferred
default via 192.168.29.1  dev wlx1cbfce1488ce  metric 600
```

Distinct public IPs, verified per interface with `curl --interface` (which uses
`SO_BINDTODEVICE`, bypassing the metric preference):

| Provider | Interface | Public IPv4 |
|---|---|---|
| Airtel (ethernet) | `enp2s0` | `223.185.62.253` |
| Jio (WiFi) | `wlx1cbfce1488ce` | `49.47.152.53` |

Each interface also carries its ISP's IPv6 prefix (`2401:4900:…` Airtel, `2405:201:…` Jio),
and the prefixes rotate — so IPv6 is not a stable anchor.

Earlier in the day the wired address moved `192.168.1.4` → `192.168.1.2` under plain DHCP,
which is why the reservations matter: AdGuard binds a *specific* address and fails to start
if it moves.

## 2.4 Reaching TickVault by IP does not work — and why it appeared to

`tickvault` is **not** `default_server`. Neither vhost declares one, so nginx falls back to
a positional rule: the first server block parsed for a listen socket becomes that socket's
default. Parse order comes from the glob `include /etc/nginx/sites-enabled/*`, which expands
alphabetically:

```
beus          ← parsed first, owns 0.0.0.0:80
tickvault
```

Measured:

```
http://192.168.1.2/                      → "BeUs · Beonedge workspace"  (461 B static)
Host: tickvault.beonedge.internal        → "TickVault" Next.js app      (11,937 B)
```

### The IPv6 quirk (real, but NOT how clients actually reach the app)

The vhosts declare different sockets:

```
beus:       listen 80;
tickvault:  listen 80;  listen [::]:80;
```

`listen [::]:80` is a **separate socket** on which `tickvault` is the sole occupant — and
therefore its default. So the defaults differ by address family:

```
v4 (0.0.0.0:80) → BeUs
v6 ([::]:80)    → TickVault
```

That asymmetry is worth removing, because the same URL can serve different sites to
different clients. But it is **not** the mechanism by which the workstation reaches
TickVault — see the next section. An earlier version of this document claimed it was; that
was wrong.

## 2.4a How clients actually reach TickVault — by name, via `/etc/hosts`

The workstation has a static mapping:

```
/etc/hosts:  100.122.85.101 tickvault.beonedge.internal
```

So no DNS is involved at all — not AdGuard, not MagicDNS, not split DNS. The browser
connects to the **Tailscale IPv4** address on `0.0.0.0:80` and sends
`Host: tickvault.beonedge.internal`, which nginx matches against `server_name`, selecting
the vhost correctly. Entirely deliberate.

This is also why dashboard access survived the whole network upheaval: `100.122.85.101` is
the tailnet address, independent of both ISPs and both LAN subnets.

**Two claims in an earlier draft were wrong and are corrected here:**

1. *"Tailscale access works by accident via the IPv6 default server."* False for this
   workstation — the hosts entry is IPv4-only and carries the correct Host header.
2. *"Tailscale split-DNS forwards `.internal` to the Jio router."* Unfounded.
   `tailscale dns status` reports **no Split DNS routes** and no custom resolvers. The VPS's
   earlier `192.168.29.2` answer came from its own `/etc/hosts` line — since commented out
   (`# 192.168.29.2 tickvault.beonedge.internal`), and the same query now returns
   *not found*. The first answer was a resolved cache hit, not the router.

### DNS does not forward HTTP

Worth stating precisely, because the loose phrasing invites a wrong mental model. AdGuard's
rewrite only **answers a name lookup** with `192.168.29.2`. The *browser* then opens its own
TCP connection to that address and sends `Host: tickvault.beonedge.internal`; nginx selects
the vhost from that header. Nothing is proxied or forwarded by DNS.

The consequence is the useful part: the vhost is reachable from **any** address that lands
on the box — LAN IP, Tailscale IP, or hosts-file entry — provided the Host header is right.
`default_server` only governs requests whose Host matches nothing. The vhost's own comment
puts it exactly: *"this vhost is only reachable by NAME."*

Fix, when wanted — make it explicit in the `tickvault` vhost:

```nginx
listen 80 default_server;
listen [::]:80 default_server;
```

This is a deliberate change of which site is the catch-all, not a pure fix: `beus` would
then answer only to `beus.beonedge.in` (fine, since it arrives via the Cloudflare tunnel
with the correct Host).

## 2.5 Static routes and the dual-router question

The Airtel router's "Static Route" blurb about saving time and bandwidth is misleading.
Static routes only tell a router how to reach a network that is not directly attached, via a
gateway on one that is. They **cannot** influence which ISP the VPS uses for outbound
traffic — the VPS's own routing table (metrics) decides that.

**Routing Airtel → `192.168.29.0/24` is unnecessary.** `192.168.29.2` is not a location, it
is a second address on the *same* VPS. Airtel clients reach identical services at
`192.168.1.2`. What they lack is name resolution, not a route.

The right fix is to bind AdGuard to both addresses and add a per-LAN rewrite:

```yaml
- "192.168.1.2:53:53/udp"    # serve Airtel clients
- "192.168.29.2:53:53/udp"   # serve Jio clients
```
```
*.beonedge.internal → 192.168.1.2      (Airtel)
*.beonedge.internal → 192.168.29.2     (Jio)
```

If inter-LAN reachability is ever genuinely wanted (a NAS or printer on the other side), the
static route alone is **not** sufficient: a Jio-side device replying to `192.168.1.x` sends
it to the Jio router, which knows nothing of that network — asymmetric routing. It needs
either a reciprocal static route on the Jio router (`192.168.1.0/24 via 192.168.29.2`) or
NAT on the VPS. The VPS is half-ready: `net.ipv4.ip_forward = 1`, but
`ufw DEFAULT_FORWARD_POLICY="DROP"` blocks forwarding — and that DROP is currently the thing
preventing the capture server from becoming a transit router between two ISP networks.

## 2.6 Static IP and SEBI order punching

Airtel is due a static public IP; Jio stays dynamic. Relevant facts:

- **The capture backend has no static-IP code.** `backend/app` and `backend/tests` contain
  zero references to `static_ip` / `KITE_STATIC_IP` / whitelisting. `build_kite_http_client`
  no longer takes `static_ip`/`proxy`, and `static_ip_configured` is gone from the session
  status payload. Remaining mentions are documentation only. The docs record why:

  > Kite's static-IP whitelist (Apr 2026) applies only to order-placement endpoints, which
  > this service does not use.

- So the static IP requires **no configuration** on the capture side. The whitelisting
  concern belongs to the separate order-placement service.

- **Static routes on the router are irrelevant to it.** What matters is egress: which
  interface outbound order traffic leaves by. Airtel already wins on metric.

- **The real risk is silent failover.** If the wired link drops, the VPS fails over to Jio's
  *dynamic* IP. Capture keeps running (desirable), but orders would reach Kite from a
  non-whitelisted IP and be **rejected**, with no obvious indication that routing was the
  cause. Capture wants failover; order placement wants to fail loudly. The fix belongs in
  the order service — bind its outbound socket to the Airtel source (`SO_BINDTODEVICE` on
  `enp2s0`, or `ip rule` policy routing) — plus a health check that alerts when egress is
  not the whitelisted IP.

## 2.7 Current infrastructure state

| | |
|---|---|
| App version running | v0.1.26 (both containers healthy) |
| Repo `__version__` | 0.1.26 |
| nginx | active, enabled; vhosts `beus`, `tickvault`; no `default_server` |
| Port 53 | free on LAN IPs — `systemd-resolved` binds only `127.0.0.53` / `127.0.0.54` |
| Port 443 | no listener; no local TLS |
| cloudflared | active, enabled — serves `beus.beonedge.in` |
| AdGuard | container removed; config retained on disk; bindings still reference `192.168.29.2` |
| ufw | active; rules unread (needs root) |
| `ip_forward` | 1, but ufw forward policy is DROP |

## 2.8 Open items

- Bind AdGuard to both LAN addresses and add the per-LAN rewrite, then re-point each
  router's DNS.
- Verify `ufw` permits `53/udp`+`53/tcp` from both LANs, and `8080` for the admin UI —
  DNS fails silently otherwise.
- Confirm AdGuard's config files actually survived (`confdir` is root-only `drwx------`, so
  a non-root `du` reported only the directory inode).
- Decide whether `tickvault` should be an explicit `default_server`. This is now a
  tidiness/robustness question rather than an access one — clients reach the app by name
  (hosts file today, AdGuard rewrite when it returns), so the only thing `default_server`
  changes is which site answers an unmatched Host, and it would remove the v4/v6 asymmetry.
- Deploy the frontend changes from `89e363b` onward (aligned grid, expanded session
  history) — built but not yet shipped.
- Confirm the first clean capture stop records the day's true totals in session history,
  which is the first live test of the restart-resume fix.
- Deferred: manual frontend/backend deploy procedure doc; delta encoding for option grids
  (`option_grid_delta` scaffolding exists but is dead code).
