#!/usr/bin/env bash
# Build-machine deploy:
#   ./release_manager/deploy.sh              -> compose up HERE (local stack)
#   ./release_manager/deploy.sh --ship KEY   -> rsync the staged bundle to the VPS
#                                               (preserving the VPS .env) and run
#                                               the shipped DATA_DOWNLOADER/deploy.sh
#
# Local paths are script-driven and version-controlled (see LOCAL_* below). The
# VPS is fully env-driven via its own DATA_DOWNLOADER/.env.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RECENT_DIR="$RELEASE_DIR/recent_builds"
RELEASE_ENV="$RELEASE_DIR/.env"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
BUNDLE_DIR=""
SHIP_KEY=""
DOCKER=()

# Script-driven, version-controlled local stack paths (used only for local up).
LOCAL_STACK_ROOT="$ROOT_DIR/.local_stack"
LOCAL_MARKET_DATA="$LOCAL_STACK_ROOT/MARKET_DATA"
LOCAL_ARCHIVE_DATA="$LOCAL_STACK_ROOT/z_market_data"

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

usage() {
    cat <<'USAGE'
Usage: ./release_manager/deploy.sh [--bundle DIR]
       ./release_manager/deploy.sh --ship SSH_KEY [--bundle DIR]
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE_DIR="${2:?--bundle requires a directory}"; shift 2 ;;
        --ship) SHIP_KEY="${2:?--ship requires an SSH key}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

resolve_bundle() {
    [[ -z "$BUNDLE_DIR" ]] || { BUNDLE_DIR="$(cd "$BUNDLE_DIR" && pwd)"; return; }
    local -a bundles=()
    mapfile -t bundles < <(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '.export-*' -print | sort)
    [[ ${#bundles[@]} -eq 1 ]] || {
        printf 'Expected exactly one staged bundle in %s (found %s). Use --bundle.\n' \
            "$RECENT_DIR" "${#bundles[@]}" >&2; exit 1;
    }
    BUNDLE_DIR="${bundles[0]}"
}

release_config() {
    local key=$1 value="${!1:-}"
    [[ -n "$value" || ! -f "$RELEASE_ENV" ]] || value="$(env_value "$RELEASE_ENV" "$key")"
    printf '%s' "$value"
}

deploy_local() {
    require_file "$BACKEND_ENV"; require_file "$FRONTEND_ENV"
    command -v git >/dev/null || { echo "git is required." >&2; exit 1; }
    mapfile -d '' -t DOCKER < <(docker_engine_command)
    "${DOCKER[@]}" compose version >/dev/null

    # Never disrupt a running local capture.
    local market_open market_close tz
    market_open="$(env_value "$BACKEND_ENV" MARKET_OPEN)"; [[ -n "$market_open" ]] || market_open=09:00
    market_close="$(env_value "$BACKEND_ENV" MARKET_CLOSE)"; [[ -n "$market_close" ]] || market_close=15:30
    tz="$(env_value "$BACKEND_ENV" TIMEZONE)"; [[ -n "$tz" ]] || tz=Asia/Kolkata
    assert_outside_capture_window "$market_open" "$market_close" "$tz"
    assert_capture_stopped "$BACKEND_ENV" false

    # Script-driven local data roots (created here; version-controlled defaults).
    mkdir -p "$LOCAL_MARKET_DATA" "$LOCAL_ARCHIVE_DATA"
    printf 'Local stack data roots:\n  MARKET_DATA_PATH=%s\n  ARCHIVE_DATA_PATH=%s\n' \
        "$LOCAL_MARKET_DATA" "$LOCAL_ARCHIVE_DATA"

    printf 'Composing up the local stack (build)...\n'
    MARKET_DATA_PATH="$LOCAL_MARKET_DATA" ARCHIVE_DATA_PATH="$LOCAL_ARCHIVE_DATA" \
    APP_VERSION="$(env_value "$BACKEND_ENV" APP_VERSION)" \
        "${DOCKER[@]}" compose --project-directory "$ROOT_DIR" -f "$ROOT_DIR/compose.yaml" \
        --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" up -d --build
    health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || {
        echo "Local stack failed health checks." >&2; exit 1;
    }
    printf 'Local stack is up and healthy.\n'
}

ship_bundle() {
    local bundle_dir=$1 ssh_user ssh_host deploy_dir remote
    for cmd in jq sha256sum rsync ssh; do
        command -v "$cmd" >/dev/null || { printf '%s is required.\n' "$cmd" >&2; exit 1; }
    done
    [[ -f "$SHIP_KEY" ]] || { echo "SSH key is missing: $SHIP_KEY" >&2; exit 1; }
    verify_bundle_sha256 "$bundle_dir"
    ssh_user="$(release_config VPS_SSH_USER)"
    ssh_host="$(release_config VPS_SSH_HOST)"
    deploy_dir="$(release_config VPS_DEPLOY_DIR)"
    [[ "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { echo "VPS_SSH_USER is missing/invalid." >&2; exit 1; }
    [[ -n "$ssh_host" ]] || { echo "VPS_SSH_HOST is missing." >&2; exit 1; }
    [[ "$deploy_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$deploy_dir" != *".."* ]] || {
        echo "VPS_DEPLOY_DIR must be a safe absolute path." >&2; exit 1;
    }
    remote="${ssh_user}@${ssh_host}"
    local ssh_cmd=(ssh -i "$SHIP_KEY" -o IdentitiesOnly=yes)

    printf 'Ensuring remote deploy dir %s...\n' "$deploy_dir"
    "${ssh_cmd[@]}" "$remote" "mkdir -p $(printf '%q' "$deploy_dir")"

    printf 'Syncing bundle to %s:%s (preserving remote .env)...\n' "$remote" "$deploy_dir"
    rsync -az --delete --exclude='.env' \
        -e "ssh -i $(printf '%q' "$SHIP_KEY") -o IdentitiesOnly=yes" \
        "$bundle_dir/" "$remote:$deploy_dir/"

    printf 'Running the remote deploy...\n'
    "${ssh_cmd[@]}" "$remote" \
        "cd $(printf '%q' "$deploy_dir") && chmod +x deploy.sh rollback.sh && ./deploy.sh"
    printf 'Shipped and deployed %s on %s.\n' "$(release_bundle_version "$bundle_dir")" "$remote"
}

acquire_release_lock "$(global_release_lock_file)"
if [[ -n "$SHIP_KEY" ]]; then
    resolve_bundle
    ship_bundle "$BUNDLE_DIR"
else
    deploy_local
fi
