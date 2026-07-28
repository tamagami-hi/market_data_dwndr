# nginx vhost — install, update, verify

Operational guide for the TickVault reverse-proxy vhost. The config itself (with inline
explanations of every directive) lives at:

    deploy/nginx/tickvault.beonedge.internal.conf

Design rationale — why a proxy is required at all, and how it relates to the same-origin
frontend — is in [lan-and-public-domain-routing.md](./lan-and-public-domain-routing.md).

| | |
|---|---|
| Host | `beonedge@100.122.85.101` (Tailscale) — LAN `192.168.29.2` |
| Live config | `/etc/nginx/sites-available/tickvault` |
| Enabled via | symlink `/etc/nginx/sites-enabled/tickvault` |
| Logs | `/var/log/nginx/tickvault.{access,error}.log` |
| Serves | `http://tickvault.beonedge.internal` (LAN only) |

nginx runs on the **host**, not in a container, and forwards to the app containers over
host loopback (`127.0.0.1:3789` frontend, `127.0.0.1:9000` backend).

---

## 1. First-time install

```bash
# From the build machine
scp -i ~/.ssh/beonedge_vps \
  deploy/nginx/tickvault.beonedge.internal.conf \
  beonedge@100.122.85.101:/tmp/tickvault-new.conf
```

```bash
# On the VPS
sudo cp /tmp/tickvault-new.conf /etc/nginx/sites-available/tickvault
sudo ln -s /etc/nginx/sites-available/tickvault /etc/nginx/sites-enabled/tickvault
sudo nginx -t                    # never skip
sudo systemctl reload nginx
```

---

## 2. Updating an existing config

```bash
# On the VPS
sudo cp -p /etc/nginx/sites-available/tickvault \
           /etc/nginx/sites-available/tickvault.bak-$(date +%Y%m%d%H%M%S)

sudo cp /tmp/tickvault-new.conf /etc/nginx/sites-available/tickvault

sudo nginx -t                    # only proceed if "test is successful"
sudo systemctl reload nginx
```

The symlink already exists after a first-time install, so there is nothing to re-link.

**`reload`, not `restart`.** Reload starts workers with the new config and retires the
old ones gracefully, so the Cloudflare-tunnelled `beus.beonedge.in` site keeps serving
with no dropped connections. `restart` briefly drops everything on port 80.

**`nginx -t` before every reload.** It parses the whole config and reports the exact file
and line on error, without touching the running server. If it fails, nothing has changed
yet — the old config is still live.

---

## 3. Verify

### Routing (run on the VPS; the `Host` header stands in for DNS)

```bash
curl -s -o /dev/null -w 'login %{http_code}\n' -H 'Host: tickvault.beonedge.internal' http://127.0.0.1/login
curl -s -o /dev/null -w 'api   %{http_code}\n' -H 'Host: tickvault.beonedge.internal' http://127.0.0.1/api/auth/status
curl -s -o /dev/null -w 'beus  %{http_code}\n' -H 'Host: beus.beonedge.in'            http://127.0.0.1/
```

All three should be `200`. The third is the **regression check** — it proves the change
did not disturb the tunnelled public site sharing this nginx.

### Headers: compression, version hiding, hardening

```bash
curl -s -D- -o /dev/null \
  -H 'Host: tickvault.beonedge.internal' -H 'Accept-Encoding: gzip' \
  http://127.0.0.1/api/stats | grep -iE 'content-encoding|^server|x-content-type'
```

Expect `content-encoding: gzip`, a `Server:` line **without** a version number, and
`x-content-type-options: nosniff`.

### WebSocket upgrade (expect `101`)

```bash
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 \
  -H 'Host: tickvault.beonedge.internal' \
  -H 'Origin: http://tickvault.beonedge.internal' \
  -H 'Upgrade: websocket' -H 'Connection: Upgrade' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Version: 13' \
  http://127.0.0.1/ws/capture-status
```

`101 Switching Protocols` validates the whole chain at once: the `map $http_upgrade`
block, the `Upgrade`/`Connection` headers, **and** the backend accepting the browser's
`Origin` against `FRONTEND_URL`.

### From a LAN client (real end-to-end, DNS included)

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://tickvault.beonedge.internal/login
```

If DNS is not set up on the machine you are testing from, force it while keeping the
`Host` header intact:

```bash
curl --resolve tickvault.beonedge.internal:80:192.168.29.2 \
     http://tickvault.beonedge.internal/login
```

---

## 4. Validate a config without touching the live one

Useful when you cannot get a sudo prompt, or want to check a candidate before installing.
Runs a real nginx binary in a throwaway container (`conf.d/*.conf` is included inside
nginx's `http` block, which is exactly where this vhost belongs):

```bash
docker run --rm \
  -v /tmp/tickvault-new.conf:/etc/nginx/conf.d/tickvault.conf:ro \
  nginx:alpine nginx -t
```

---

## 5. Rollback

```bash
sudo cp /etc/nginx/sites-available/tickvault.bak-<timestamp> \
        /etc/nginx/sites-available/tickvault
sudo nginx -t && sudo systemctl reload nginx
```

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `502 Bad Gateway` | Upstream down or wrong port. Check `docker ps` and `curl http://127.0.0.1:9000/health` on the box. |
| Page loads, "backend offline", `/api` 404 | You browsed a container port directly (e.g. `:3789`). Same-origin **requires** going through nginx on port 80. |
| WebSocket reconnects forever | Missing `Upgrade`/`Connection` headers, or the browser's `Origin` is not in the backend's `FRONTEND_URL`. Note the Origin includes the **port** — `…:8080` is a different origin from `…` (port 80). |
| Every `/api` call 404s | A trailing slash on `proxy_pass` (`http://upstream/`) strips the matched prefix, turning `/api/stats` into `/stats`. |
| `NXDOMAIN` in the browser | DNS, not nginx. Check the device is using AdGuard (`192.168.29.2`), and that Private DNS / browser secure DNS (DoH) is off. |
| Wrong site served | `Host` header did not match any `server_name`, so nginx used the default server (the alphabetically-first site in `sites-enabled`). |

Log to check first:

```bash
sudo tail -30 /var/log/nginx/tickvault.error.log
```

Expect one benign warning per WebSocket connection:
`upstream sent duplicate header line: "date: …"` — that is uvicorn emitting a lowercase
`date` header alongside nginx's; nginx ignores it and the handshake still succeeds.

---

## 7. Things not to change

- **`listen 80`** — binds `0.0.0.0`, which already includes the LAN IP. Narrowing it to
  `listen 192.168.29.2:80` breaks the Cloudflare tunnel, because `cloudflared` connects
  to `http://localhost:80`.
- **`127.0.0.1` upstreams** — keeps the containers private and leaves
  `curl localhost:9000` working on the box.
- **No `location /monitor`** — the frontend owns that route; the backend also serves a
  `/monitor` page and routing it upstream would shadow the real dashboard.
- **`proxy_pass` without a trailing slash** — the backend's routes genuinely start with
  `/api`, so the prefix must not be stripped.

Two inheritance traps that make the config look repetitive on purpose:

- **`proxy_set_header` replaces, it does not merge.** A `location` that declares any
  header loses every one inherited from `server`. That is why each location repeats the
  full `Host` / `X-Forwarded-*` set.
- **`add_header` behaves the same way**, hence `always` and server-level placement.

And two directives that are silently inert if you forget their companion:

- **`keepalive` in `upstream`** does nothing unless `proxy_set_header Connection "";` is
  set in the proxying location — otherwise nginx sends `Connection: close`. Deliberately
  **not** set in `/ws/`, where `Connection` must carry the upgrade.
- **`gzip on`** does nothing for proxied responses unless `gzip_proxied` is set (default
  is `off`), and every response in this vhost is proxied.

---

## 8. When the domain changes (e.g. a public static IP)

No frontend rebuild is needed — the image is same-origin and therefore
domain/scheme-agnostic. Three runtime changes:

1. `server_name` in this config → the new hostname; `nginx -t` && `reload`.
2. `FRONTEND_URL` in `/srv/dev_stack/DATA_DOWNLOADER/.env` → must include the new
   browser origin; restart the backend. Without it, routing works but every WebSocket
   is rejected by the `Origin` check.
3. DNS → point the new name at the host.

For HTTPS additionally: a certificate on `listen 443 ssl`, an 80→443 redirect, and the
`https://` origin in `FRONTEND_URL`. The frontend derives `wss://` automatically from
`window.location`, so still no rebuild.
