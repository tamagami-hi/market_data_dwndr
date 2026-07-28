# DATA_DOWNLOADER — VPS operator guide

This folder is a **self-contained deployment** of the TickVault market-data
downloader (backend + frontend). It is shipped here by the developer's build
machine and can **deploy and roll back itself** — there is no source checkout,
no build tools, and no internet build step on this machine.

You normally never run anything by hand: the developer ships an update from the
build machine and it deploys automatically. This guide is for first-time setup,
manual operation, rollback, and troubleshooting.

---

## 1. What is in this folder

```text
/srv/dev_stack/DATA_DOWNLOADER/
├── docker-compose.yml     image-based compose (no build); reads ./.env
├── .env                   YOUR production config + secrets (create from .env.example)
├── .env.example           template (safe to read; never contains real secrets)
├── images/
│   ├── backend.tar.gz      compressed backend image (loaded into Docker, then idle)
│   └── frontend.tar.gz     compressed frontend image
├── deploy.sh              self-contained deploy runner
├── rollback.sh            self-contained rollback runner
├── manifest.json          release id, image tags/ids, sha256 checksums
├── version.json           the active release id
└── README.md              this file
```

Nothing here holds live market data. The three data locations are **env-driven**
in `.env` and live **outside** this folder (this folder is replaced on updates):

| Purpose | `.env` key | Typical path |
| --- | --- | --- |
| SSD live capture | `MARKET_DATA_PATH` | `/srv/dev_stack/data/MARKET_DATA` |
| Compressed archive | `ARCHIVE_DATA_PATH` | `/srv/backup/DATA_DOWNLOADER_ARCHIVE` |
| Incoming images (deploy) | `RELEASE_IMAGE_PATH` | `/srv/dev_stack/DATA_DOWNLOADER/images` |
| Previous images (rollback) | `ROLLBACK_IMAGE_PATH` | `/srv/backup/DATA_DOWNLOADER_ROLLBACKS` |

The running images themselves live in Docker's store (`/var/lib/docker`), loaded
from `RELEASE_IMAGE_PATH/*.tar.gz` by `deploy.sh`.

---

## 2. First-time setup (once)

```bash
cd /srv/dev_stack/DATA_DOWNLOADER

# 1) create your production env from the template and lock it down
cp .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
#    fill: KITE_API_KEY/SECRET, KITE_USER_ID/PASSWORD, KITE_TOKEN_BROKER_PASSCODE,
#          KITE_RATE_BROKER_URL, RISK_FREE_RATE (fallback),
#          RELEASE_MAINTENANCE_TOKEN  (generate: openssl rand -hex 32)
#    set the env-driven paths (MARKET_DATA_PATH, ARCHIVE_DATA_PATH, RELEASE_IMAGE_PATH, ROLLBACK_IMAGE_PATH)
#    leave networking as localhost unless you have a static IP/domain

# 2) create the data + rollback directories and give them the container's UID:GID
#    (APP_UID:APP_GID in .env, default 10001:10001)
sudo mkdir -p /srv/dev_stack/data/MARKET_DATA \
              /srv/backup/DATA_DOWNLOADER_ARCHIVE \
              /srv/backup/DATA_DOWNLOADER_ROLLBACKS
sudo chown -R 10001:10001 /srv/dev_stack/data/MARKET_DATA /srv/backup/DATA_DOWNLOADER_ARCHIVE

# 3) first bring-up
./deploy.sh
```

`.env` is **preserved on every future update** — you only fill it once.

---

## 3. Reaching the UI

The stack binds to `HOST_BIND_ADDRESS` (default `127.0.0.1`, loopback). From your
laptop, tunnel the two ports over SSH, then browse locally:

```bash
ssh -i ~/.ssh/beonedge_vps -L 3789:localhost:3789 -L 9000:localhost:9000 beonedge@<vps>
# then open http://localhost:3789
```

To expose it on the office LAN instead, set `HOST_BIND_ADDRESS=0.0.0.0` and point
`FRONTEND_URL` + `NEXT_PUBLIC_BACKEND_URL` at the reachable address (the frontend
image must be rebuilt for a new `NEXT_PUBLIC_BACKEND_URL`). Note: `0.0.0.0` exposes
the **unauthenticated** API on the network — only do this on a trusted network.

---

## 4. Deploy / update

Updates are pushed by the developer (`deploy.sh --ship` from the build machine),
which rsyncs a new bundle here (never overwriting `.env`) and runs `./deploy.sh`.
To run a deploy manually after a manual rsync:

```bash
./deploy.sh
```

`deploy.sh` will, in order: verify the image checksums against `manifest.json`,
**refuse to run during the capture window** (`MARKET_OPEN`–`MARKET_CLOSE`), drain
capture writers via a maintenance lease, **save the currently running images to
`ROLLBACK_IMAGE_PATH/<current release>/`**, `docker load` the new images, bring the
stack up with `--no-build`, health-check `/health` and `/login`, and **roll back
automatically if the health check fails**. It never touches `.env` or the data
mounts.

---

## 5. Rollback

```bash
./rollback.sh                 # restore the newest saved previous release
./rollback.sh <release_id>    # restore a specific saved release
```

It loads the previous images from `ROLLBACK_IMAGE_PATH`, brings them up, and
health-checks. List what is available:

```bash
ls -1 "$(sed -n 's/^ROLLBACK_IMAGE_PATH=//p' .env | tail -1)"
```

---

## 6. Status, logs, control

```bash
# active release
cat version.json ; sed -n 's/^APP_VERSION=//p' .env

# container status + logs (use sudo if your user lacks the docker group)
docker compose --env-file .env -f docker-compose.yml ps
docker compose --env-file .env -f docker-compose.yml logs -f backend
docker compose --env-file .env -f docker-compose.yml logs -f frontend

# operational status of the downloader itself
curl -s http://127.0.0.1:9000/api/status?format=text
```

Do **not** run `docker compose down` during market hours — capture must flush its
writers first. Prefer letting the deploy/rollback runners manage the lifecycle.

---

## 7. Troubleshooting

- **`missing bundle file: .../.env`** → you haven't created `.env` yet (step 2).
- **Deploy refused: capture window** → wait until after `MARKET_CLOSE`, or the next
  non-trading day. This is intentional; it protects an in-progress capture.
- **`could not acquire the capture maintenance lease`** → `RELEASE_MAINTENANCE_TOKEN`
  in `.env` must be 32–256 URL-safe chars and match what the running backend expects.
- **Health check failed → auto-rolled back** → check `docker compose … logs backend`;
  the previous release is restored automatically on an update.
- **`MARKET_DATA_PATH does not exist`** → create the dir and `chown` it to `APP_UID:APP_GID`.
- **Disk** → old rollback images accumulate under `ROLLBACK_IMAGE_PATH`; prune the
  oldest `<release_id>/` dirs when you no longer need them for rollback.
