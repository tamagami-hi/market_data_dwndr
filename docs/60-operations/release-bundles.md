---
title: DATA_DOWNLOADER Release Bundles
area: operations
type: runbook
status: active
tags: [area/operations, type/runbook]
---

# DATA_DOWNLOADER release bundles

The downloader ships as two immutable Docker images in a checksummed,
secret-free bundle. See `release_manager/README.md` for commands and bundle
layout.

## Release invariant

A shippable bundle must originate from a clean checkout whose `HEAD` equals
`origin/main`. Its release identifier combines the source commit and the public
Docker build configuration. The manifest pins both Docker image IDs and hashes
the compressed image archives and Compose file. An offline Ed25519 private key
signs it; the VPS holds only the public verification key. Any mismatch blocks
deployment.

## VPS invariant

The checkout lives at `/srv/dev_stack/market_data_dwndr`. Live raw data stays on
the SSD under the configured project path; verified zstd archives stay under
`/srv/data/z_market_data` on the HDD. These paths, application credentials,
ports, and URLs remain in the VPS env files and are neither exported nor restored
by release tooling.

The current home deployment is reachable over Tailscale. There is intentionally
no Nginx configuration in the release bundle.

## Safe deployment order

1. Verify the bundle and exact VPS checkout commit.
2. Run the existing storage/network preflight.
3. Refuse deployment during configured capture hours and acquire a host `flock`.
4. Acquire persistent backend maintenance, block new capture starts, and flush writers.
5. Inspect each archive for its one expected tag, then load and verify image IDs.
6. Save the current active bundle through staged replacement for rollback.
7. Start the selected release without building on the VPS.
8. Require both health checks, release maintenance, then advance metadata.

Rollback performs the same validation and health gate in reverse. Neither path
uses `docker compose down -v`, deletes bind-mounted data, or replaces env files.
An existing legacy stack without a signed active bundle must be stopped after EOD
before the first bundle deployment; tooling will not invent an unsafe rollback
artifact for it.
