# Release manager

This is the source-clone variant of the BeOnEdge image release flow. It preserves the
VPS `.env` files, builds immutable images tagged with the exact `origin/main` commit
plus a hash of all Docker build configuration, health-checks both services,
and retains previous image tags for rollback. Unlike the
BeOnEdge stack, it does not export tar bundles because this project is cloned directly
under `/srv/dev_stack/market_data_dwndr`.

The home VPS release publishes only on its Tailscale address. This project deliberately
ships no Nginx configuration while the host has no static public IP.

```bash
./release_manager/status.sh
./release_manager/deploy.sh
./release_manager/rollback.sh             # latest previous image
./release_manager/rollback.sh <release-tag> # explicit existing image tag
```

`deploy.sh` refuses dirty, behind, or ahead worktrees. Run `git pull --ff-only` first.
It checks capture state both before building and immediately before restart. An existing
deployment whose capture state is unreachable or malformed is treated as unsafe. It
never replaces either env file and never removes the SSD/HDD bind-mounted data.
Coordinated handoff of an actively downloading session belongs to the separate rollover
phase.
