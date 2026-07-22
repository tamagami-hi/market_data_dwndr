# VPS Docker deployment

This deployment keeps live `.bin` files on the SSD and publishes verified zstd archives
to the HDD without changing the internal market-data layout.

| Purpose | VPS path |
|---|---|
| Git checkout and live data | `/srv/dev_stack/market_data_dwndr` |
| Live data root | `/srv/dev_stack/market_data_dwndr/MARKET_DATA` |
| Durable zstd root | `/srv/data/z_market_data` |

For example, `MARKET_DATA/INDICES/NIFTY/2026-07-22.bin` becomes
`/srv/data/z_market_data/INDICES/NIFTY/2026-07-22.bin.zst`. Session state and instrument
metadata stay on the SSD; only `.bin` market-data files are archived.

## 1. One-time VPS provisioning

The current VPS has Docker Engine, but the `beonedge` user cannot access its socket,
Compose v2 is not installed, and both `/srv` mount roots are root-owned. Run these steps
interactively with sudo:

```bash
sudo apt-get update
sudo apt-get install docker-compose-plugin

sudo install -d -o beonedge -g beonedge /srv/dev_stack/market_data_dwndr

git clone https://github.com/tamagami-hi/market_data_dwndr.git \
  /srv/dev_stack/market_data_dwndr

sudo install -d -o 10001 -g 10001 -m 0750 \
  /srv/dev_stack/market_data_dwndr/MARKET_DATA
sudo install -d -o 10001 -g 10001 -m 0750 \
  /srv/data/z_market_data
```

Do not add an account to the `docker` group casually: Docker socket access is effectively
root access. The commands below therefore use `sudo docker compose`.

Confirm that the HDD is mounted before starting containers:

```bash
findmnt -T /srv/data/z_market_data
```

The target must be `/srv/data`, not `/`. Otherwise archives could silently consume the
SSD/root filesystem.

## 2. Clone and configure

```bash
cd /srv/dev_stack
cd market_data_dwndr

cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
chmod 600 backend/.env frontend/.env.local
```

Set at least these backend values in `backend/.env`:

```dotenv
MARKET_DATA_PATH=/srv/dev_stack/market_data_dwndr/MARKET_DATA
ARCHIVE_DATA_PATH=/srv/data/z_market_data
HTTP_HOST=0.0.0.0
HTTP_PORT=<backend-port>
HOST_BIND_ADDRESS=<tailscale-ip>
FRONTEND_URL=http://<tailscale-ip>:<frontend-port>
APP_UID=10001
APP_GID=10001
```

Also set the Kite credentials, the rotated broker passcode, and `KITE_USER_ID`. Set in
`frontend/.env.local`:

```dotenv
NEXT_PUBLIC_BACKEND_URL=http://<tailscale-ip>:<backend-port>
PORT=<frontend-port>
E2E_FRONTEND_PORT=<unused-test-port>
```

Ports and storage paths are read only from these two ignored env files. Compose is
invoked with both files so it uses those same values for image builds, host publishing,
and bind mounts.

## 3. Home-VPS network access

This home VPS has no static public IP and does not use Nginx. Bind both published ports
to its Tailscale address (`<tailscale-ip>`) as shown above, then restrict those ports to
the intended tailnet users/devices with Tailscale ACLs. The backend is not exposed on a
public interface.

Because Docker cannot publish to an address that is not present yet, ensure Tailscale is
online before starting the stack after a reboot. A host service can order the release
start after `tailscaled.service`; until that is added, verify with `tailscale status` and
run `./release_manager/deploy.sh` after boot.

If direct tailnet access is unavailable, bind both ports to `127.0.0.1`, rebuild the
frontend with a localhost backend URL, and use an SSH tunnel:

```bash
ssh -i ~/.ssh/beonedge_vps \
  -L <frontend-port>:127.0.0.1:<frontend-port> \
  -L <backend-port>:127.0.0.1:<backend-port> \
  beonedge@<tailscale-ip>
```

Do not publish the backend to the public internet: CORS/Origin checks are not
authentication, and capture control is an administrative capability. If this VPS gains
a public/static IP later, add TLS and an authenticated edge as a separate deployment
change.

The shared token broker solves authentication, not Kite's static-egress requirement.
`KITE_STATIC_IP` only selects an address already present on this host; it cannot turn a
dynamic home connection into a static public IP. Live Kite REST/WebSocket capture from
this VPS may therefore require `KITE_HTTP_PROXY`, a VPN/exit path through the cloud VPS,
or another whitelisted static-egress service. That network rollover is intentionally
deferred to the next phase.

## 4. Validate, build, and start

```bash
PROJECT_DIR=/srv/dev_stack/market_data_dwndr ./deploy/preflight.sh

./release_manager/status.sh
./release_manager/deploy.sh
```

The release manager verifies that the checkout is clean and exactly matches
`origin/main`, builds images tagged with the commit plus a non-secret hash of
`NEXT_PUBLIC_BACKEND_URL`, `APP_UID`, and `APP_GID`, checks capture state again
immediately before restart, health-checks both services, and records the prior image
tag for rollback. The config hash prevents changed browser-visible routing or host file
ownership from reusing stale images. It preserves both VPS env files and both data
mounts.

Use raw Compose only for read-only inspection. Do not replace `deploy.sh` with a manual
`up --build`: that bypasses capture-state, immutable-tag, health, and rollback gates.

```bash

sudo docker compose \
  --env-file backend/.env \
  --env-file frontend/.env.local \
  config --quiet

sudo docker compose \
  --env-file backend/.env \
  --env-file frontend/.env.local \
  ps
```

Inspect service logs without printing the env files:

```bash
sudo docker compose \
  --env-file backend/.env \
  --env-file frontend/.env.local \
  logs --tail=100 backend frontend
```

Changing `NEXT_PUBLIC_BACKEND_URL` changes the release tag and rebuilds the frontend
because Next.js embeds public variables during `next build`.

## 5. Updates and rollback

```bash
git pull --ff-only
./release_manager/deploy.sh

# Restore the most recently replaced image pair without touching market data:
./release_manager/rollback.sh
```

Before deployment, rotate the broker passcode and revoke/regenerate the Kite token that
were previously exposed. Never put either value in Compose, Dockerfiles, build args, or
tracked files; they belong only in the mode-0600 backend env file.
