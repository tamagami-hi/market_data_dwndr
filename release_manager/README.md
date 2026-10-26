# release_manager

The deployment pipeline for the downloader (backend + frontend) on the home VPS (`beonedge`). It is the same pipeline as the centralised broker login service's: one control center, a tracked path contract, and VPS-native scripts that own every docker command there.

**Run one script:**

```bash
./release_manager/status.sh
```

`status.sh` shows a dashboard, then a menu: **Repository → Provision → Export → Ship + Deploy**. Every operation in the menu also exists as a non-interactive command, which is what an agent or a phone session uses:

```bash
./release_manager/status.sh --status                 # dashboard, then exit
./release_manager/status.sh --diagnose               # readiness; exit 1 on blockers
./release_manager/status.sh --do sync --yes          # main ↔ origin/main
./release_manager/status.sh --do release patch       # bump, commit, tag, push
./release_manager/status.sh --do export
./release_manager/status.sh --do deploy --yes
./release_manager/status.sh --do rollback --list
./release_manager/status.sh --do rollback --latest --yes
./release_manager/status.sh --do reload --yes        # pick up an edited .env
./release_manager/status.sh --do logs
```

Without a terminal, any operation that would prompt needs `--yes`. `status.sh --help` lists every operation.

There is no local deploy step: **Export, then Ship + Deploy.** Export proves both images start before they are packed, and the VPS deploy gates on their health.

## Where things live on the VPS

| What | Path |
| --- | --- |
| Stack directory (compose, scripts, images, `.env`, the live `MARKET_DATA`) | `/srv/dev_stack/DATA_DOWNLOADER` |
| Rollback archives | `/srv/backup/DATA_DOWNLOADER/backup/rollback/images/<version>` |
| Deploy and app logs | `/srv/backup/DATA_DOWNLOADER/logs/{deploy,app}` |
| Compose project and containers | `data-downloader`: `market-data-dwndr-backend`, `market-data-dwndr-frontend` |

[`stacks/data_downloader/paths.json`](stacks/data_downloader/paths.json) is the **only** authority for these paths. No script derives a remote path. To change a path, edit the contract, then validate it (Provision → Validate) and re-export.

Moving from the pipeline before the contract is a one-time step on the VPS, described in [`APP_GUIDE.md`](stacks/data_downloader/APP_GUIDE.md#moving-from-the-old-layout-once). Ship refuses to deploy until it is done.

## The stages

```
Repository      GIT     this computer   commit, integrate branches, sync main, PRs
Release         GIT     this computer   bump __version__, commit, tag, push main + tag
Export          BUILD   this computer   builds both images, proves they start, stages a bundle
Ship + Deploy   SHIP    this computer   uploads the bundle over Tailscale, invokes the VPS script
<stack>_deploy  RUN     the VPS         every docker command lives here, under a lock
```

- **Releases come from main only**, and origin/main is what ships. The Repository workflow brings work onto main: commit, merge a remote branch that is ahead, sync with origin/main, approve open PRs into main.
- **The version advances in one place.** `backend/app/__init__.py` (`__version__`) is the canonical version. Export only reads it: a clean tree exactly on tag `vX.Y.Z` builds `X.Y.Z`; anything else builds `<next>-dev.N.gSHA[.dirty]`. A tag on HEAD that names another version than `__version__` stops Export.
- **Release moves `__version__`, the tag and origin together.** `status.sh --do release patch|minor|major [--yes] [--no-push]`, or Export → *Cut a release*, which lists the three with the version each would cut. It needs a clean tree on main that is not behind origin/main (it fetches first), and a version above every existing tag. It rewrites `__version__`, commits only that file as `chore(release): vX.Y.Z` with the repository's git identity, tags that commit `vX.Y.Z`, and pushes main and the tag in one atomic push: both land or neither. A refused push keeps the commit and tag here and prints the retry. `--no-push` stops after the tag. It builds nothing.
- **All docker work happens on the VPS**, in `data_downloader_deploy.sh` and `data_downloader_rollback.sh`. Given only SSH, the stack directory can be deployed or rolled back by hand (`APP_GUIDE.md`).
- **Checksums are verified three times:** locally, after upload, and on arrival inside the VPS script.
- **The images must prove themselves before they ship.** Each runs read-only with no network: the backend on throwaway settings must answer `/health`, the frontend must serve `/login`, and both must run as a non-root user.

## The deploy, step by step (on the VPS)

Lock → release dir → backup tree → disk space → version reconcile → checksums → docker and `.env` present → **capture guard** → confirm → archive the outgoing images → load the new images → **drain the capture writers** → `compose up` → **health gate** (both containers `healthy`) → record the version → prune.

- **The capture guard** refuses inside the capture window and while capture is running, because a restart then loses ticks for good. `--force` bypasses it for a stuck stack.
- **The writer drain** takes the release-maintenance lease before the containers are replaced, and releases it once the new release is healthy.
- If the health gate fails, the deploy **rolls back the images automatically** to the previous release.
- Reload and rollback restart the backend too, so the same guard and drain apply to them.

`.env` belongs to the operator: no script reads, writes or removes it. The few values the guards need are read inside the running backend, from the environment Docker gave it.

## This box is shared

The VPS also runs the `boe-*`, `cbl-*` and `aie-*` stacks. Every docker command here is scoped to this compose project, its container names or its own `market-data-dwndr-*` image tags. Nothing prunes globally. Deploys use the Tailscale alias `beonedge` from `~/.ssh/config`, never the public SSH alias.

## Layout

```
status.sh                the only entry point: dashboard, menu and --do
lib/
  ui.sh                  output helpers
  version.sh             version labels from __version__ and the vX.Y.Z tags
  stacks.sh              stack identity and ssh plumbing (no paths)
  hosts.sh               host identity: which box, which ssh alias (no default host)
  paths.sh               path contract validation and lookup
  remote_state.sh        one read-only SSH round trip for the dashboard
  ops_repo.sh            commit, integrate, sync main, pull requests
  ops_release.sh         bump __version__, commit, tag vX.Y.Z, push
  ops_provision.sh       create the VPS tree; print the sudo fix when blocked
  ops_export.sh          build, accept, save and stage a bundle
  ops_ship.sh            upload and invoke the VPS-native deploy
  ops_operate.sh         rollback, reload, logs, containers, diagnose, resources
stacks/
  _shared/               the VPS-native runtime (_dd_lib, _dd_deploy, _dd_rollback)
  data_downloader/       paths.json, compose, entry points, .env.example, APP_GUIDE.md
tests/release_test.sh    the pipeline's tests (no VPS, no real docker)
build/                   staged bundles (gitignored)
state/                   local ship ledger (gitignored)
```

The build reads `backend/.env` and `frontend/.env.local` for the build settings (`APP_UID`, `APP_GID`, `NEXT_PUBLIC_APP_NAME`); the frontend is built same-origin unless `--do export --backend-url <origin>` is given. Secrets never enter a bundle.

The shell scripts here carry comments on purpose. Each safety gate encodes a reason that is expensive to rediscover during an incident.
