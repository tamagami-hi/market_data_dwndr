# DATA_DOWNLOADER — VPS operator guide

This folder is the deployed TickVault market-data downloader (backend + frontend). The build machine ships every release here with `release_manager/status.sh`. The folder can also deploy and roll back by itself over nothing but SSH: there is no source checkout and no build step on this machine.

You normally never run anything here by hand. This guide is for the one-time move, manual operation, rollback and troubleshooting.

## Where things live

[`paths.json`](paths.json) is the only authority for these paths. No script derives one.

| What | Path |
| --- | --- |
| Stack: compose, scripts, `paths.json`, `manifest.json`, `version.json`, `.env` | `/srv/dev_stack/DATA_DOWNLOADER` |
| Incoming image archives | `/srv/dev_stack/DATA_DOWNLOADER/images` |
| Live capture (bind-mounted; `MARKET_DATA_PATH` in `.env`) | `/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA` |
| Compressed archive (bind-mounted; `ARCHIVE_DATA_PATH` in `.env`) | `/srv/data/DATA_DOWNLOADER/ARCHIVE` |
| Rollback archives, one folder per release | `/srv/backup/DATA_DOWNLOADER/backup/rollback/images/<version>` |
| Deploy logs, and container logs captured on a failure | `/srv/backup/DATA_DOWNLOADER/logs/{deploy,app}` |
| Compose project and containers | `data-downloader`: `market-data-dwndr-backend`, `market-data-dwndr-frontend` |
| Lock shared by deploy, reload and rollback | `/run/lock/data-downloader.lock` |

`.env` belongs to you. No script reads, writes or removes it: only Docker reads it, through `env_file` and compose interpolation. A deploy uploads files by name, never with `--delete`, so `.env`, `MARKET_DATA` and the archive are never touched.

## Moving from the old layout (once)

The pipeline before `paths.json` kept its rollbacks in `/srv/backup/DATA_DOWNLOADER/ROLLBACKS`, its own `deploy.sh` and `rollback.sh` in this folder, and three deploy settings in `.env`. A deploy refuses to run until the move is done, and `status.sh --diagnose` reports it.

Nothing is restarted by the move, so it is safe at any hour. Everything involved is owned by `beonedge`, so no sudo is needed.

1. Create the new backup tree and move the saved releases into it. `mv` on the same disk is instant and keeps each folder's date, which is how rollback and pruning order them.

   ```bash
   mkdir -p /srv/backup/DATA_DOWNLOADER/backup/rollback/images /srv/backup/DATA_DOWNLOADER/logs/deploy /srv/backup/DATA_DOWNLOADER/logs/app
   mv /srv/backup/DATA_DOWNLOADER/ROLLBACKS/* /srv/backup/DATA_DOWNLOADER/backup/rollback/images/
   rmdir /srv/backup/DATA_DOWNLOADER/ROLLBACKS
   ```

2. Remove the old scripts and the old guide from the stack folder. The next ship brings `data_downloader_deploy.sh`, `data_downloader_rollback.sh` and this guide.

   ```bash
   cd /srv/dev_stack/DATA_DOWNLOADER && rm deploy.sh rollback.sh README.md
   ```

3. Edit `.env` and delete these three lines. `paths.json` now says where images and rollbacks live, and the deploy passes the version to compose itself:

   ```
   APP_VERSION=...
   RELEASE_IMAGE_PATH=...
   ROLLBACK_IMAGE_PATH=...
   ```

   Nothing else in `.env` changes for the move.

4. From the build machine, `./release_manager/status.sh --diagnose` should report no blocking issues.

The running release keeps running throughout. The first deploy afterwards archives it into the new tree (it was never archived before: the old deploy saved only the release it replaced), then keeps the newest 5 archives (`retention.keep_releases` in `paths.json`) and prunes the rest, oldest first.

## Deploy, by hand

The build machine runs this for you. After a ship-only upload:

```bash
cd /srv/dev_stack/DATA_DOWNLOADER && ./data_downloader_deploy.sh
```

In order: lock → release folder → backup tree → disk space → versions → checksums → docker and `.env` present → **capture guard** → confirm → **archive the outgoing release** → load the new images → **drain the capture writers** → `compose up` → **health gate** → record the version → prune.

- **Capture guard.** It refuses inside the capture window (`MARKET_OPEN`–`MARKET_CLOSE` in `MARKET_TIMEZONE`, as the running backend has them) and while capture is running, because a restart then loses ticks for good.
- **Writer drain.** It takes the release-maintenance lease so the writers flush and pause. The token is read inside the running backend, from its own environment.
- **Health gate.** Both containers must report Docker's own `healthy` (the compose healthchecks probe `/health` and `/login`) within 180 s, without restarting.
- **Auto-rollback.** If the gate fails, the previous release's images come back from its archive and start.

`--force` redeploys the same version and bypasses the window and the running check, for a stuck stack that must be replaced now. The writers are still drained if the backend answers; the health gate and auto-rollback still apply. `--reload` recreates the running release's containers so an edited `.env` takes effect, under the same guards.

## Rollback

```bash
./data_downloader_rollback.sh --list                 # what is saved, oldest first
./data_downloader_rollback.sh                        # the newest saved release other than the running one
./data_downloader_rollback.sh --to 0.1.53            # a specific one
```

It loads that release's images and, when its archive has one, the compose file that started them. It archives the release it replaces first, so you can move forward again. The same capture guard applies (`--force` bypasses it). Archives from the old pipeline (two image archives and `version.txt`, named like `v0.1.51`) restore too, under the current compose file.

## Status, logs, control

```bash
jq . version.json                                     # what runs, what ran before, and the last attempt
docker compose -p data-downloader ps
docker logs -f market-data-dwndr-backend
ls -1t /srv/backup/DATA_DOWNLOADER/logs/deploy | head  # deploy logs
```

Do **not** run `docker compose down` during market hours: capture must flush its writers first. Let the deploy and rollback scripts manage the lifecycle.

## Troubleshooting

- **`refusing during the capture window`**: wait for the close, or use `--force` if the stack is stuck.
- **`could not take the capture maintenance lease`**: `RELEASE_MAINTENANCE_TOKEN` in `.env` must be 32–256 URL-safe characters, the same one the running backend started with.
- **`the containers were not healthy within 180s` → auto-rolled back**: read the captured logs in `/srv/backup/DATA_DOWNLOADER/logs/app`.
- **`bind source path does not exist`**: `MARKET_DATA_PATH` or `ARCHIVE_DATA_PATH` is missing; create it and `chown` it to `APP_UID:APP_GID`.
- **`another deploy or rollback holds /run/lock/data-downloader.lock`**: one is still running; wait for it.
