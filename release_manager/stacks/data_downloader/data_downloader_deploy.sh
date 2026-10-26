#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# data_downloader_deploy.sh — VPS-native deploy entry point.
#
# Runs ON THE VPS, in the stack directory. Invoked by the operator machine
# (release_manager/status.sh --do deploy) or by hand:
#
#     ssh beonedge 'cd /srv/dev_stack/DATA_DOWNLOADER && ./data_downloader_deploy.sh'
#
# All logic is shared. This file declares identity and policy only.
#
# Policy: confirmation required. A deploy restarts the live capture service, so
# it is never silent.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=../_shared/_dd_lib.sh
source "$HERE/_dd_lib.sh"
# shellcheck source=../_shared/_dd_deploy.sh
source "$HERE/_dd_deploy.sh"

DD_REQUIRE_CONFIRM=true

dd_deploy_main "$HERE/paths.json" "$@"
