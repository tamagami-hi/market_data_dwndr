# DATA_DOWNLOADER release manager

This is the BeOnEdge-style image release flow for `market_data_dwndr`. It builds
the exact clean `origin/main` commit, exports both Docker images into one
checksummed release bundle, deploys that bundle locally or over SSH, and keeps
complete prior bundles for rollback.

No Nginx configuration is generated or installed. The home VPS remains private
on Tailscale.

## Layout

```text
release_manager/
├── export.sh                 build and export immutable images
├── deploy.sh                 local deploy or VPS ship
├── rollback.sh               restore a verified prior bundle
├── status.sh                 read-only local/remote status
├── .env.example              non-secret VPS connection template
├── recent_builds/            staged bundle (runtime, ignored)
├── DATA_DOWNLOADER/          active bundle metadata/images (runtime, ignored)
└── rollback/                 prior complete bundles (runtime, ignored)
```

Every release identifier is `<12-char-git-sha>-<12-char-build-config-hash>`.
The build hash covers the public frontend backend URL and container UID/GID.
`manifest.json` records the full commit, image tags and image IDs, Compose hash,
and SHA-256 for every image archive. It is signed with an offline Ed25519 private
key; the VPS verifies it with only the public key. Deployment refuses modified
artifacts, dirty provenance, unexpected archive tags, partial tag collisions,
and mismatched checkouts.

## Configuration

Production secrets remain only in:

- `backend/.env`
- `frontend/.env.local`
- `release_manager/.env` for VPS connection details

All three are ignored. Export reads the first two to derive the image build but
never copies any environment file into a bundle. Deploy and rollback preserve
them in place. The SSD live path and HDD archive path therefore remain entirely
environment-controlled.

```bash
cp release_manager/.env.example release_manager/.env
```

Generate the release key pair once using the commands in `.env.example`. Keep
the private key only on the trusted build machine and configure only the public
key path on the VPS. Also generate a separate 32+ character
`RELEASE_MAINTENANCE_TOKEN` in `backend/.env`; it authenticates the internal
drain lease and is never part of a release bundle.

## Build and deploy

```bash
# Clean main must exactly match origin/main.
./release_manager/export.sh

# Deploy the single staged bundle on this host.
./release_manager/deploy.sh

# Ship the same bundle to the configured VPS checkout.
./release_manager/deploy.sh --ship ~/.ssh/beonedge_vps

# If multiple staged bundles exist, select one explicitly.
./release_manager/deploy.sh --bundle release_manager/recent_builds/<bundle>
```

The VPS checkout must already be at the manifest commit and have its ignored env
files and data directories prepared. Shipping transfers no source, secrets, or
market data; it transfers only the image bundle, then runs the checked-out deploy
script. Docker Compose must be installed and the operator must be allowed to use
Docker without an interactive sudo prompt.

Deployment checks that capture is stopped before replacing containers. It loads
images only when an existing immutable tag is absent or has the exact recorded
image ID, starts with `--no-build`, health-checks backend and frontend, and only
then advances `DATA_DOWNLOADER` and `APP_VERSION`. A failed release restores the
prior bundle or stops a failed first deployment.

Deploy and rollback are permitted only outside `MARKET_OPEN`–`MARKET_CLOSE`.
They take a host-wide `flock`, acquire a persistent backend maintenance lease,
block new capture starts, and wait for writer flush before Compose replacement.
The lease survives container restart and is released after health checks; its
bounded backend TTL provides crash recovery.

A legacy running Compose stack with no signed `DATA_DOWNLOADER` bundle is not a
safe rollback source and is therefore blocked. After the final capture and EOD
compression, stop that legacy stack without deleting volumes, then perform the
first signed bundle deployment.

## Status and rollback

```bash
./release_manager/status.sh
./release_manager/status.sh --remote ~/.ssh/beonedge_vps
./release_manager/rollback.sh
./release_manager/rollback.sh --rollback-dir release_manager/rollback/<bundle>
```

Rollback validates checksums, saves the current release for forward recovery,
loads the selected images, health-checks them, and updates active metadata only
after success. It never removes Docker volumes, SSD/HDD bind mounts, or env files.

## Verification

```bash
bash release_manager/tests/common_test.sh
bash release_manager/tests/export_test.sh
bash -n release_manager/*.sh release_manager/lib/*.sh release_manager/tests/*.sh \
  release_manager/tests/fixtures/*.sh
```
