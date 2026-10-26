#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# data_downloader_rollback.sh — VPS-native rollback entry point.
#
# Runs ON THE VPS, in the stack directory. Invoked by the operator machine
# (release_manager/status.sh --do rollback) or by hand:
#
#     ssh beonedge 'cd /srv/dev_stack/DATA_DOWNLOADER && ./data_downloader_rollback.sh --list'
#
# All logic is shared. This file declares identity only.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=../_shared/_dd_lib.sh
source "$HERE/_dd_lib.sh"
# shellcheck source=../_shared/_dd_rollback.sh
source "$HERE/_dd_rollback.sh"

dd_rollback_main "$HERE/paths.json" "$@"
