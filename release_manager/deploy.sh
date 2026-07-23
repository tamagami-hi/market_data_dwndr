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
LOCAL_MARKET_DATA="$RELEASE_DIR/DATA_DOWNLOADER/MARKET_DATA"
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

copy_active_bundle_to_rollback() {
    local active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    [[ -f "$active_dir/version.json" ]] || { printf 'No active release yet — nothing to snapshot.\n'; return 0; }
    [[ -f "$active_dir/images/backend.tar.gz" ]] || { printf 'Active release has no images — nothing to snapshot.\n'; return 0; }

    local version stamp snapshot_dir
    version="$(jq -r '.version' "$active_dir/version.json" 2>/dev/null || echo unknown)"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    snapshot_dir="$RELEASE_DIR/rollback/${version:-unknown}-${stamp}"
    mkdir -p "$snapshot_dir"

    for file in docker-compose.yml .env version.json manifest.json README.md deploy.sh rollback.sh; do
        [[ -f "$active_dir/$file" ]] && cp "$active_dir/$file" "$snapshot_dir/$file"
    done
    [[ -f "$active_dir/images/backend.tar.gz" ]] && cp "$active_dir/images/backend.tar.gz" "$snapshot_dir/backend.tar.gz"
    [[ -f "$active_dir/images/frontend.tar.gz" ]] && cp "$active_dir/images/frontend.tar.gz" "$snapshot_dir/frontend.tar.gz"
    
    printf 'Snapshotted active release %s -> rollback/%s\n' "${version:-unknown}" "$(basename "$snapshot_dir")"
}

deploy_local() {
    command -v git >/dev/null || { echo "git is required." >&2; exit 1; }
    command -v jq >/dev/null || { echo "jq is required." >&2; exit 1; }
    mapfile -d '' -t DOCKER < <(docker_engine_command)
    "${DOCKER[@]}" compose version >/dev/null

    resolve_bundle
    local active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    local active_env="$active_dir/.env"

    if [[ ! -f "$active_env" ]]; then
        if [[ -f "$BUNDLE_DIR/.env" ]]; then
            cp "$BUNDLE_DIR/.env" "$active_env"
        elif [[ -f "$active_dir/.env.example" ]]; then
            cp "$active_dir/.env.example" "$active_env"
        else
            echo "No .env found in DATA_DOWNLOADER to use." >&2
            exit 1
        fi
        set_env_value "$active_env" MARKET_DATA_PATH "$LOCAL_MARKET_DATA"
        set_env_value "$active_env" ARCHIVE_DATA_PATH "$LOCAL_ARCHIVE_DATA"
        set_env_value "$active_env" RELEASE_IMAGE_PATH "$active_dir/images"
    fi

    # Never disrupt a running local capture.
    local market_open market_close tz
    market_open="$(env_value "$active_env" MARKET_OPEN)"; [[ -n "$market_open" ]] || market_open=09:00
    market_close="$(env_value "$active_env" MARKET_CLOSE)"; [[ -n "$market_close" ]] || market_close=15:30
    tz="$(env_value "$active_env" TIMEZONE)"; [[ -n "$tz" ]] || tz=Asia/Kolkata
    assert_outside_capture_window "$market_open" "$market_close" "$tz"
    assert_capture_stopped "$active_env" false

    mkdir -p "$(env_value "$active_env" MARKET_DATA_PATH)" "$(env_value "$active_env" ARCHIVE_DATA_PATH)"

    copy_active_bundle_to_rollback

    printf 'Staging bundle into DATA_DOWNLOADER...\n'
    if [[ -f "$active_dir/docker-compose.yml" ]]; then
        printf 'Stopping existing local stack...\n'
        (cd "$active_dir" && "${DOCKER[@]}" compose down)
    fi

    mkdir -p "$active_dir/images"
    cp "$BUNDLE_DIR/docker-compose.yml" "$active_dir/docker-compose.yml"
    cp "$BUNDLE_DIR/version.json" "$active_dir/version.json"
    cp "$BUNDLE_DIR/manifest.json" "$active_dir/manifest.json"
    cp "$BUNDLE_DIR/images/backend.tar.gz" "$active_dir/images/backend.tar.gz"
    cp "$BUNDLE_DIR/images/frontend.tar.gz" "$active_dir/images/frontend.tar.gz"

    set_env_value "$active_env" APP_VERSION "$(jq -r '.version' "$BUNDLE_DIR/version.json")"

    printf 'Loading images...\n'
    "${DOCKER[@]}" load -i "$active_dir/images/backend.tar.gz"
    "${DOCKER[@]}" load -i "$active_dir/images/frontend.tar.gz"

    printf 'Composing up the local stack in DATA_DOWNLOADER...\n'
    (cd "$active_dir" && "${DOCKER[@]}" compose up -d)

    health_check_stack "$active_env" "$active_env" || {
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

    printf 'Syncing bundle to %s:%s (preserving remote .env and MARKET_DATA)...\n' "$remote" "$deploy_dir"
    # -v lists each file as it is sent (current + already-synced); --info=progress2
    # shows a single overall progress bar with percent, rate and ETA; --partial keeps
    # partial files so a dropped transfer of the large image tarballs resumes.
    rsync -azh --delete --partial --info=progress2 -v \
        --exclude='.env' --exclude='MARKET_DATA' --exclude='ROLLBACKS' --exclude='ARCHIVE' \
        -e "ssh -i $(printf '%q' "$SHIP_KEY") -o IdentitiesOnly=yes" \
        "$bundle_dir/" "$remote:$deploy_dir/"

    printf 'Running the remote deploy...\n'
    "${ssh_cmd[@]}" "$remote" \
        "cd $(printf '%q' "$deploy_dir") && chmod +x deploy.sh rollback.sh && ./deploy.sh"
    printf 'Shipped and deployed %s on %s.\n' "$(release_bundle_version "$bundle_dir")" "$remote"
}

acquire_release_lock "$(global_release_lock_file)"
if [[ -n "$SHIP_KEY" ]]; then
    active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    [[ -f "$active_dir/manifest.json" ]] || { echo "Nothing staged in DATA_DOWNLOADER to ship. Run local deploy first." >&2; exit 1; }
    ship_bundle "$active_dir"
else
    deploy_local
fi
