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
AUTH_POLL_INTERVAL_SECONDS=60
MARKET_OPEN=09:00
MARKET_CLOSE=15:30
# Populate with official exchange closure dates before each calendar year.
MARKET_HOLIDAYS=2026-08-15,2026-10-02
APP_UID=10001
APP_GID=10001
```

Target runtime settings are `KITE_API_KEY`, `KITE_EXPECTED_USER_ID`, the Calspread session
URL/passcode, rate source/fallback, storage, and network settings. The current code still
requires `KITE_API_SECRET`, `KITE_USER_ID`, and `KITE_PASSWORD` for its legacy direct-login
path; treat those as transitional compatibility fields pending removal, not target deployment
credentials. Set in `frontend/.env.local`:

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

Because Docker cannot publish to an address that is not present yet, the tracked
`deploy/market-data-dwndr.service.example` orders stack startup after
`tailscaled.service`, Docker, network readiness, and both storage mounts. Its preflight
also confirms that the configured `HOST_BIND_ADDRESS` is currently assigned by
Tailscale before Compose starts.

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
change (see `docs/60-operations/lan-and-public-domain-routing.md`).

This project only downloads market data, so it does **not** need a static egress IP:
Kite's static-IP whitelist (Apr 2026) applies only to order-placement endpoints, which
live in the separate `algo_engine` service. Existing-session retrieval/validation goes through
calspread.online (our own AWS-hosted platform) and never required a static IP.

## 4. Validate, build, and start

Releases are built on the build machine and shipped as image bundles; the VPS needs no
checkout and builds nothing. From the build machine:

```bash
./release_manager/status.sh --diagnose     # the VPS is ready, the layout is the contract's
./release_manager/status.sh --do export    # build both images, prove they start, stage a bundle
./release_manager/status.sh --do deploy    # ship it and run the VPS-native deploy
```

The VPS-native deploy (`data_downloader_deploy.sh`) refuses inside the capture window and
while capture is running, drains the capture writers through the release-maintenance
lease, archives the running images for rollback, health-gates both containers, and rolls
back by itself if they are not healthy. It never touches `.env` or the data mounts. See
`release_manager/README.md` and `release_manager/stacks/data_downloader/APP_GUIDE.md`.

Use raw Compose only for read-only inspection. Do not replace the deploy with a manual
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

## 5. Enable automatic boot startup

Install the unit after the release manager has built and selected immutable images:

```bash
sudo cp deploy/market-data-dwndr.service.example \
  /etc/systemd/system/market-data-dwndr.service
sudo systemctl daemon-reload
sudo systemctl enable --now market-data-dwndr.service
sudo systemctl status market-data-dwndr.service
```

The unit runs `deploy/preflight.sh`, then starts the existing image tags with
`docker compose up -d --no-build`. It does not pull source, build images, or replace the
release manager. On shutdown it stops the containers so writer shutdown receives the
normal Compose grace period. After changing env files or deploying new images, use the
release manager as usual; use `sudo systemctl restart market-data-dwndr.service` only
when explicitly restarting the already-selected images.

If the checkout or mount locations differ from the documented VPS layout, copy the
example and change `WorkingDirectory`, `Environment=PROJECT_DIR`,
`ConditionPathExists`, and `RequiresMountsFor` before installation.

## 6. Updates and rollback

```bash
./release_manager/status.sh --do release patch   # on main: bump, commit, tag, push
./release_manager/status.sh --do export
./release_manager/status.sh --do deploy

# Restore the newest archived release without touching market data:
./release_manager/status.sh --do rollback --latest
```

Before deployment after credential exposure, rotate the Calspread endpoint passcode and have an
administrator revoke/relogin through Calspread so it publishes a different Kite token. Repeated
retrieval alone does not regenerate the token. Never put either value in Compose, Dockerfiles,
build args, or tracked files; the target architecture scopes the passcode to `md-control` and
the access token to the control/uplink credential mount. See
[[broker-session-authentication]].
