#!/usr/bin/env bash
# Trigger a rollback on the VPS. The actual rollback logic lives in the shipped,
# self-contained DATA_DOWNLOADER/rollback.sh (restores the previous release from
# ROLLBACK_IMAGE_PATH). Env files and data mounts are never touched.
#
# Usage:
#   ./release_manager/rollback.sh --ship SSH_KEY [release_id]
#
# Omit release_id to restore the newest saved previous release. You can also run
# ./rollback.sh directly on the VPS inside the DATA_DOWNLOADER folder.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RELEASE_ENV="$RELEASE_DIR/.env"
SHIP_KEY=""
TARGET=""

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ship) SHIP_KEY="${2:?--ship requires an SSH key}"; shift 2 ;;
        --help|-h) echo "Usage: ./release_manager/rollback.sh --ship SSH_KEY [release_id]"; exit 0 ;;
        *) [[ -z "$TARGET" ]] && { TARGET="$1"; shift; } || { echo "Unknown arg: $1" >&2; exit 1; } ;;
    esac
done

[[ -n "$SHIP_KEY" ]] || {
    echo "Run on the VPS: cd <deploy dir> && ./rollback.sh [release_id]" >&2
    echo "Or from here:   ./release_manager/rollback.sh --ship <key> [release_id]" >&2
    exit 1
}
[[ -f "$SHIP_KEY" ]] || { echo "SSH key is missing: $SHIP_KEY" >&2; exit 1; }

release_config() {
    local key=$1 value="${!1:-}"
    [[ -n "$value" || ! -f "$RELEASE_ENV" ]] || value="$(env_value "$RELEASE_ENV" "$key")"
    printf '%s' "$value"
}
ssh_user="$(release_config VPS_SSH_USER)"
ssh_host="$(release_config VPS_SSH_HOST)"
deploy_dir="$(release_config VPS_DEPLOY_DIR)"
[[ "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ && -n "$ssh_host" \
    && "$deploy_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$deploy_dir" != *".."* ]] || {
    echo "VPS_SSH_USER / VPS_SSH_HOST / VPS_DEPLOY_DIR missing or invalid in release_manager/.env." >&2
    exit 1
}

printf 'Rolling back on %s@%s...\n' "$ssh_user" "$ssh_host"
ssh -i "$SHIP_KEY" -o IdentitiesOnly=yes "${ssh_user}@${ssh_host}" \
    "cd $(printf '%q' "$deploy_dir") && ./rollback.sh $(printf '%q' "$TARGET")"
