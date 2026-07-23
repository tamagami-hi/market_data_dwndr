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
        --help|-h) 
            echo "Usage: ./release_manager/rollback.sh [--ship SSH_KEY] [release_id]"
            echo "If --ship is not provided, runs a local rollback for DATA_DOWNLOADER."
            exit 0 ;;
        *) [[ -z "$TARGET" ]] && { TARGET="$1"; shift; } || { echo "Unknown arg: $1" >&2; exit 1; } ;;
    esac
done

rollback_local() {
    local rollback_dir="$RELEASE_DIR/rollback"
    local active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    
    [[ -d "$rollback_dir" ]] || { echo "No rollbacks directory found at $rollback_dir" >&2; exit 1; }
    
    local target_dir=""
    if [[ -n "$TARGET" ]]; then
        target_dir="$rollback_dir/$TARGET"
    else
        mapfile -t entries < <(find "$rollback_dir" -mindepth 1 -maxdepth 1 -type d | sort -r)
        [[ ${#entries[@]} -gt 0 ]] || { echo "No rollback snapshots found in $rollback_dir" >&2; exit 1; }
        echo "Available local rollback snapshots:"
        local display=() entry version
        for entry in "${entries[@]}"; do
            version="unknown"
            [[ -f "$entry/version.json" ]] && version="$(jq -r '.version // "unknown"' "$entry/version.json" 2>/dev/null)"
            display+=("$(basename "$entry") | version=$version")
        done
        select choice in "${display[@]}"; do
            [[ -n "${choice:-}" ]] && { target_dir="${entries[$((REPLY-1))]}"; break; }
            echo "Invalid selection"
        done
    fi

    [[ -d "$target_dir" ]] || { echo "Snapshot not found: $target_dir" >&2; exit 1; }
    printf 'Restoring snapshot %s to DATA_DOWNLOADER...\n' "$(basename "$target_dir")"

    if [[ -f "$active_dir/docker-compose.yml" ]]; then
        printf 'Stopping existing stack...\n'
        (cd "$active_dir" && docker compose down)
    fi

    rm -rf "$active_dir/images"
    rm -f "$active_dir/docker-compose.yml" "$active_dir/README.md" "$active_dir/version.json" "$active_dir/manifest.json" "$active_dir/deploy.sh" "$active_dir/rollback.sh"
    mkdir -p "$active_dir/images"
    
    cp "$target_dir/docker-compose.yml" "$active_dir/docker-compose.yml"
    cp "$target_dir/version.json" "$active_dir/version.json"
    cp "$target_dir/manifest.json" "$active_dir/manifest.json"
    [[ -f "$target_dir/.env" ]] && cp "$target_dir/.env" "$active_dir/.env"
    cp "$target_dir/backend.tar.gz" "$active_dir/images/backend.tar.gz"
    cp "$target_dir/frontend.tar.gz" "$active_dir/images/frontend.tar.gz"
    [[ -f "$target_dir/README.md" ]] && cp "$target_dir/README.md" "$active_dir/README.md"
    [[ -f "$target_dir/deploy.sh" ]] && cp "$target_dir/deploy.sh" "$active_dir/deploy.sh"
    [[ -f "$target_dir/rollback.sh" ]] && cp "$target_dir/rollback.sh" "$active_dir/rollback.sh"

    printf 'Loading restored images...\n'
    docker load -i "$active_dir/images/backend.tar.gz"
    docker load -i "$active_dir/images/frontend.tar.gz"

    printf 'Starting restored stack...\n'
    (cd "$active_dir" && docker compose up -d)
    
    printf 'Local rollback completed successfully.\n'
}

if [[ -z "$SHIP_KEY" ]]; then
    rollback_local
    exit 0
fi

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
