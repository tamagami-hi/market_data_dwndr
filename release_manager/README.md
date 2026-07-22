# DATA_DOWNLOADER release manager

Image-based release flow for the downloader (backend + frontend). The build
machine builds both images and assembles **one self-contained `DATA_DOWNLOADER`
bundle**; that folder is the **only** thing shipped to the VPS and it can deploy
and roll back **itself** there — no git checkout, no source, no build tools on
the VPS.

> **Two docs, two audiences.** This file is the **developer / build-machine**
> guide (build, run locally, ship, roll back from your workstation). The
> **VPS operator** guide lives in `DATA_DOWNLOADER/README.md` and is shipped
> inside the bundle so it is always present on the server.

Backend and frontend run on the **same machine**, so — exactly like local dev —
they talk over `localhost`; the same images run locally and on the VPS. No fixed
IP is required (reach the UI on the box or via an SSH tunnel; switch to a static
IP/domain later with an env change + one rebuild).

## Layout

```text
release_manager/
├── export.sh              build images + assemble the self-contained bundle
├── deploy.sh              local: compose up HERE   |   --ship KEY: rsync + deploy on VPS
├── rollback.sh            --ship KEY: trigger the VPS rollback
├── status.sh              local + (--remote KEY) VPS status
├── compose.deploy.yaml    image-based deploy compose (shipped as docker-compose.yml)
├── .env.example           build-machine ship config (VPS_SSH_* + VPS_DEPLOY_DIR)
├── lib/common.sh          build-machine helpers
├── recent_builds/         the single staged bundle (git-ignored)
└── DATA_DOWNLOADER/       committed bundle templates + self-contained VPS runners
    ├── .env.example       the VPS production env (fill once on the VPS; preserved)
    ├── deploy.sh          self-contained VPS deploy runner
    └── rollback.sh        self-contained VPS rollback runner
```

Every release id is `<12-char git sha>-<12-char build-config hash>`. The build
hash covers `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_APP_NAME`, and the container
UID/GID. `manifest.json` records tags, image IDs, and sha256 for the compose file
and both image archives; the VPS runner re-verifies those sha256 before loading.

## Configuration

Secrets live only in git-ignored files and are never bundled:

- build machine: `backend/.env`, `frontend/.env.local`, `release_manager/.env`
- VPS: `DATA_DOWNLOADER/.env` (filled once, **preserved across every update**)

```bash
cp release_manager/.env.example release_manager/.env    # set VPS_SSH_* + VPS_DEPLOY_DIR
```

The frontend product name is env-driven: set `NEXT_PUBLIC_APP_NAME` in
`frontend/.env.local` (default `TickVault`); it is baked into the image at build.

## Build

```bash
./release_manager/export.sh
```
Builds `market-data-dwndr-{backend,frontend}:<release_id>` from the current tree
and writes one bundle to `release_manager/recent_builds/<release_id>-<stamp>/`
(images, `docker-compose.yml`, `.env.example`, `deploy.sh`, `rollback.sh`,
`manifest.json`, `version.json`, `README.txt`).

## Run locally (compose up here)

```bash
./release_manager/deploy.sh
```
Composes the stack up on this machine (build) using **script-driven, version-
controlled** local data roots under `./.local_stack/` — no env editing needed.
Refuses to run during the capture window and won't disrupt a running capture.

## Ship to the VPS

The VPS holds only `DATA_DOWNLOADER/`. Rsync excludes `.env`, so your production
secrets are preserved on every update.

### First deploy
```bash
# 1) push the bundle (this also runs the remote deploy, which will stop at the
#    missing .env on the very first run):
./release_manager/deploy.sh --ship ~/.ssh/beonedge_vps

# 2) on the VPS, fill the env once and create the data + rollback dirs:
ssh -i ~/.ssh/beonedge_vps beonedge@100.122.85.101
cd /srv/dev_stack/DATA_DOWNLOADER
cp .env.example .env && chmod 600 .env && ${EDITOR:-nano} .env
#   set KITE_* secrets, RELEASE_MAINTENANCE_TOKEN (openssl rand -hex 32), and the
#   env-driven paths: MARKET_DATA_PATH, ARCHIVE_DATA_PATH, ROLLBACK_IMAGE_PATH
sudo mkdir -p <MARKET_DATA_PATH> <ARCHIVE_DATA_PATH> /srv/backup/DATA_DOWNLOADER_ROLLBACKS
sudo chown -R 10001:10001 <MARKET_DATA_PATH> <ARCHIVE_DATA_PATH>
./deploy.sh            # first bring-up

# 3) reach the UI (localhost, same machine) via a tunnel from your laptop:
ssh -i ~/.ssh/beonedge_vps -L 3789:localhost:3789 -L 9000:localhost:9000 beonedge@100.122.85.101
#   then open http://localhost:3789
```

### Update (subsequent releases)
```bash
./release_manager/export.sh
./release_manager/deploy.sh --ship ~/.ssh/beonedge_vps
```
The VPS runner: verifies checksums, blocks during the capture window, drains
capture writers (release-maintenance lease), **saves the currently running images
to `ROLLBACK_IMAGE_PATH/<release_id>/`**, loads the new images, `compose up -d
--no-build`, health-checks, and **auto-rolls-back on failure**. Your `.env`,
data bind-mounts, and volumes are never touched.

## Rollback

```bash
# from the build machine:
./release_manager/rollback.sh --ship ~/.ssh/beonedge_vps            # newest saved previous release
./release_manager/rollback.sh --ship ~/.ssh/beonedge_vps <release_id>

# or directly on the VPS:
cd /srv/dev_stack/DATA_DOWNLOADER && ./rollback.sh
```
Loads the previous images from `ROLLBACK_IMAGE_PATH`, composes them up, and
health-checks — restoring nothing else.

## Status

```bash
./release_manager/status.sh
./release_manager/status.sh --remote ~/.ssh/beonedge_vps
```

## Going public later

When you get a static IP / domain: set `HOST_BIND_ADDRESS`, `FRONTEND_URL`, and
`NEXT_PUBLIC_BACKEND_URL` in the VPS `.env` to it, rebuild
(`NEXT_PUBLIC_BACKEND_URL` is baked at build), and re-ship. Note that binding to
`0.0.0.0` exposes the **unauthenticated** API on the network — only do that on a
trusted/whitelisted network or behind an authenticating reverse proxy.
