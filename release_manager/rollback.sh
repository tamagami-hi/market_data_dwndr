#!/usr/bin/env bash

# Restore a prior checksummed DATA_DOWNLOADER release. Environment files and
# bind-mounted market data are never copied, replaced, or deleted.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
RELEASE_ENV="$RELEASE_DIR/.env"
ACTIVE_DIR="$RELEASE_DIR/DATA_DOWNLOADER"
ROLLBACK_DIR="$RELEASE_DIR/rollback"
CURRENT_FILE="$RELEASE_DIR/current-version"
TARGET_DIR=""
LEASE_ID=""
PREPARED_ACTIVE=""

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

capture_api_base_url() {
    local bind_address backend_port
    bind_address="$(env_value "$BACKEND_ENV" HOST_BIND_ADDRESS)"
    backend_port="$(env_value "$BACKEND_ENV" HTTP_PORT)"
    [[ "$bind_address" != "0.0.0.0" ]] || bind_address=127.0.0.1
    printf 'http://%s:%s' "$bind_address" "$backend_port"
}

acquire_maintenance_lease() {
    local maintenance_token response
    validate_release_maintenance_ttl "$BACKEND_ENV" || return 1
    maintenance_token="$(env_value "$BACKEND_ENV" RELEASE_MAINTENANCE_TOKEN)"
    [[ "$maintenance_token" =~ ^[A-Za-z0-9_-]{32,256}$ ]] || {
        echo "RELEASE_MAINTENANCE_TOKEN must be 32-256 URL-safe characters." >&2
        return 1
    }
    response="$(printf 'header = "X-Release-Maintenance-Token: %s"\n' "$maintenance_token" \
        | curl -fsS --max-time 15 -X POST --config - \
            "$(capture_api_base_url)/api/capture/maintenance")" || return 1
    LEASE_ID="$(maintenance_lease_id "$response")" || return 1
    validate_maintenance_lease_remaining "$response" 540 || return 1
}

release_maintenance_lease() {
    local maintenance_token lease_id=$LEASE_ID
    [[ -n "$lease_id" ]] || return 0
    maintenance_token="$(env_value "$BACKEND_ENV" RELEASE_MAINTENANCE_TOKEN)"
    printf 'header = "X-Release-Maintenance-Token: %s"\n' "$maintenance_token" \
        | curl -fsS --max-time 10 -X DELETE --config - \
            "$(capture_api_base_url)/api/capture/maintenance/$lease_id" >/dev/null
    LEASE_ID=""
}

cleanup_rollback() {
    [[ -z "$PREPARED_ACTIVE" || ! -d "$PREPARED_ACTIVE" ]] \
        || rm -rf -- "$PREPARED_ACTIVE"
    [[ -z "$LEASE_ID" ]] || release_maintenance_lease >/dev/null 2>&1 || true
}
trap cleanup_rollback EXIT

usage() {
    cat <<'USAGE'
Usage: ./release_manager/rollback.sh [--rollback-dir DIR]

Restores the newest rollback snapshot by default. The selected snapshot is
checksum-validated before images load and again before active metadata updates.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rollback-dir) TARGET_DIR="${2:?--rollback-dir requires a directory}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *)
            if [[ -z "$TARGET_DIR" ]]; then TARGET_DIR="$1"; shift
            else printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1
            fi
            ;;
    esac
done

if [[ -z "$TARGET_DIR" ]]; then
    TARGET_DIR="$(find "$ROLLBACK_DIR" -mindepth 1 -maxdepth 1 -type d -print \
        2>/dev/null | sort -r | head -n 1)"
    [[ -n "$TARGET_DIR" ]] || { echo "No rollback snapshot is available." >&2; exit 1; }
fi
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

require_file "$BACKEND_ENV"
require_file "$FRONTEND_ENV"
for command_name in curl gzip; do
    command -v "$command_name" >/dev/null || {
        printf '%s is required.\n' "$command_name" >&2
        exit 1
    }
done
acquire_release_lock "$(global_release_lock_file)"
signing_key="$(release_key_path "$RELEASE_ENV" RELEASE_SIGNING_PUBLIC_KEY)"
validate_signed_release_bundle "$TARGET_DIR" "$signing_key"
validate_signed_release_bundle "$ACTIVE_DIR" "$signing_key"
validate_image_archive_tag "$TARGET_DIR/images/backend.tar.gz" \
    "$(jq -r '.images.backend.tag' "$TARGET_DIR/manifest.json")"
validate_image_archive_tag "$TARGET_DIR/images/frontend.tar.gz" \
    "$(jq -r '.images.frontend.tag' "$TARGET_DIR/manifest.json")"
PROJECT_DIR="$ROOT_DIR" "$ROOT_DIR/deploy/preflight.sh"
market_open="$(env_value "$BACKEND_ENV" MARKET_OPEN)"
market_close="$(env_value "$BACKEND_ENV" MARKET_CLOSE)"
timezone_name="$(env_value "$BACKEND_ENV" TIMEZONE)"
[[ -n "$timezone_name" ]] || timezone_name=Asia/Kolkata
assert_outside_capture_window "$market_open" "$market_close" "$timezone_name"
assert_capture_stopped "$BACKEND_ENV" true
mapfile -d '' -t DOCKER < <(docker_engine_command)
"${DOCKER[@]}" compose version >/dev/null

ensure_image() {
    local bundle_dir=$1 key=$2 tag archive expected_id existing_id loaded_id
    tag="$(jq -r ".images.${key}.tag" "$bundle_dir/manifest.json")"
    archive="$(jq -r ".images.${key}.archive" "$bundle_dir/manifest.json")"
    expected_id="$(jq -r ".images.${key}.image_id" "$bundle_dir/manifest.json")"
    validate_image_archive_tag "$bundle_dir/$archive" "$tag" || return 1
    existing_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag" 2>/dev/null || true)"
    [[ -z "$existing_id" || "$existing_id" == "$expected_id" ]] || {
        echo "Immutable image tag collision: $tag" >&2
        return 1
    }
    if [[ -z "$existing_id" ]]; then
        gzip -dc "$bundle_dir/$archive" | "${DOCKER[@]}" image load >/dev/null
    fi
    loaded_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag")"
    [[ "$loaded_id" == "$expected_id" ]] || {
        echo "Loaded image identity mismatch: $tag" >&2
        return 1
    }
}

compose_up() {
    local bundle_dir=$1 version=$2
    APP_VERSION="$version" "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" -f "$bundle_dir/docker-compose.yml" \
        --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" up -d --no-build
}

current_version="$(release_bundle_version "$ACTIVE_DIR")"
target_version="$(release_bundle_version "$TARGET_DIR")"
[[ "$current_version" != "$target_version" ]] || {
    echo "Selected rollback is already active: $target_version" >&2
    exit 1
}

ensure_image "$TARGET_DIR" backend
ensure_image "$TARGET_DIR" frontend
assert_capture_stopped "$BACKEND_ENV" true
assert_outside_capture_window "$market_open" "$market_close" "$timezone_name"
snapshot="$(snapshot_active_bundle "$ACTIVE_DIR" "$ROLLBACK_DIR")"
printf 'Saved current release for forward recovery: %s\n' "$snapshot"
PREPARED_ACTIVE="$(prepare_release_bundle "$TARGET_DIR" "$ACTIVE_DIR")"
validate_signed_release_bundle "$PREPARED_ACTIVE" "$signing_key"
acquire_maintenance_lease

printf 'Rolling back %s -> %s...\n' "$current_version" "$target_version"
compose_up "$PREPARED_ACTIVE" "$target_version"
if ! health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV"; then
    echo "Rollback failed health checks; restoring $current_version." >&2
    ensure_image "$ACTIVE_DIR" backend
    ensure_image "$ACTIVE_DIR" frontend
    compose_up "$ACTIVE_DIR" "$current_version"
    health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || true
    release_maintenance_lease || true
    exit 1
fi

if ! retired_active="$(activate_prepared_bundle "$PREPARED_ACTIVE" "$ACTIVE_DIR")"; then
    echo "Rollback activation failed; restoring $current_version." >&2
    compose_up "$ACTIVE_DIR" "$current_version"
    health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || true
    release_maintenance_lease || true
    exit 1
fi
PREPARED_ACTIVE=""
set_env_value "$BACKEND_ENV" APP_VERSION "$target_version"
printf '%s\n' "$target_version" > "$CURRENT_FILE"
release_maintenance_lease
[[ -z "$retired_active" ]] || rm -rf -- "$retired_active"
printf 'Rollback complete. Environment files and data mounts were unchanged.\n'
