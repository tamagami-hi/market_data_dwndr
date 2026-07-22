#!/usr/bin/env bash

# Deploy or ship one checksummed DATA_DOWNLOADER release bundle. Production env
# files and SSD/HDD data roots stay in the source checkout and are never copied.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
RELEASE_ENV="$RELEASE_DIR/.env"
RECENT_DIR="$RELEASE_DIR/recent_builds"
ACTIVE_DIR="$RELEASE_DIR/DATA_DOWNLOADER"
ROLLBACK_DIR="$RELEASE_DIR/rollback"
CURRENT_FILE="$RELEASE_DIR/current-version"
BUNDLE_DIR=""
SHIP_KEY=""
SIGNING_KEY=""
LEASE_ID=""
SHIP_ARCHIVE=""
PREPARED_ACTIVE=""

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

cleanup_deploy() {
    [[ -z "$SHIP_ARCHIVE" || ! -f "$SHIP_ARCHIVE" ]] || rm -f -- "$SHIP_ARCHIVE"
    [[ -z "$PREPARED_ACTIVE" || ! -d "$PREPARED_ACTIVE" ]] \
        || rm -rf -- "$PREPARED_ACTIVE"
    if [[ -n "$LEASE_ID" ]]; then
        release_maintenance_lease >/dev/null 2>&1 || true
    fi
}
trap cleanup_deploy EXIT

usage() {
    cat <<'USAGE'
Usage: ./release_manager/deploy.sh [--bundle DIR]
       ./release_manager/deploy.sh --ship SSH_KEY [--bundle DIR]

Without --ship, loads and health-gates a local immutable release bundle. With
--ship, transfers that same secret-free bundle to the configured VPS and invokes
its checked-out release manager. Configure VPS_SSH_USER, VPS_SSH_HOST, and
VPS_PROJECT_DIR in release_manager/.env. No Nginx files are installed.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE_DIR="${2:?--bundle requires a directory}"; shift 2 ;;
        --ship) SHIP_KEY="${2:?--ship requires an SSH private key}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

resolve_bundle() {
    local -a bundles=()
    if [[ -n "$BUNDLE_DIR" ]]; then
        BUNDLE_DIR="$(cd "$BUNDLE_DIR" && pwd)"
        return
    fi
    mapfile -t bundles < <(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d \
        ! -name '.export-*' ! -name '.incoming-*' -print | sort)
    if [[ ${#bundles[@]} -ne 1 ]]; then
        printf 'Expected exactly one staged bundle in %s, found %s. Use --bundle DIR.\n' \
            "$RECENT_DIR" "${#bundles[@]}" >&2
        exit 1
    fi
    BUNDLE_DIR="${bundles[0]}"
}

ensure_release_image() {
    local bundle_dir=$1 image_key=$2 tag archive expected_id existing_id loaded_id
    tag="$(jq -r ".images.${image_key}.tag" "$bundle_dir/manifest.json")"
    archive="$(jq -r ".images.${image_key}.archive" "$bundle_dir/manifest.json")"
    expected_id="$(jq -r ".images.${image_key}.image_id" "$bundle_dir/manifest.json")"
    validate_image_archive_tag "$bundle_dir/$archive" "$tag" || return 1
    existing_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag" 2>/dev/null || true)"
    if [[ -n "$existing_id" && "$existing_id" != "$expected_id" ]]; then
        printf 'Refusing to overwrite immutable tag %s (%s != %s).\n' \
            "$tag" "$existing_id" "$expected_id" >&2
        return 1
    fi
    if [[ "$existing_id" == "$expected_id" ]]; then
        printf 'Reusing verified image %s.\n' "$tag"
        return 0
    fi
    gzip -dc "$bundle_dir/$archive" | "${DOCKER[@]}" image load >/dev/null
    loaded_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag" 2>/dev/null || true)"
    [[ "$loaded_id" == "$expected_id" ]] || {
        printf 'Loaded image identity mismatch for %s.\n' "$tag" >&2
        return 1
    }
}

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
            "$(capture_api_base_url)/api/capture/maintenance")" || {
        echo "Could not acquire the capture maintenance lease." >&2
        return 1
    }
    LEASE_ID="$(maintenance_lease_id "$response")" || return 1
    validate_maintenance_lease_remaining "$response" 540 || return 1
    printf 'Capture maintenance lease acquired; writers are flushed.\n'
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

assert_deploy_window() {
    local market_open market_close timezone_name
    market_open="$(env_value "$BACKEND_ENV" MARKET_OPEN)"
    market_close="$(env_value "$BACKEND_ENV" MARKET_CLOSE)"
    timezone_name="$(env_value "$BACKEND_ENV" TIMEZONE)"
    [[ -n "$timezone_name" ]] || timezone_name=Asia/Kolkata
    assert_outside_capture_window "$market_open" "$market_close" "$timezone_name"
}

compose_up_bundle() {
    local bundle_dir=$1 version=$2
    APP_VERSION="$version" "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" \
        -f "$bundle_dir/docker-compose.yml" \
        --env-file "$BACKEND_ENV" \
        --env-file "$FRONTEND_ENV" \
        up -d --no-build
}

compose_down_bundle() {
    local bundle_dir=$1 version=$2
    APP_VERSION="$version" "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" \
        -f "$bundle_dir/docker-compose.yml" \
        --env-file "$BACKEND_ENV" \
        --env-file "$FRONTEND_ENV" \
        down
}

write_deployment_pointer() {
    local version=$1
    printf '%s\n' "$version" > "$CURRENT_FILE"
}

deploy_local_bundle() {
    local bundle_dir=$1 version git_sha head_sha remote_sha old_version="" snapshot=""
    local retired_active=""
    local existing_stack=false capture_check_required=false
    require_file "$BACKEND_ENV"
    require_file "$FRONTEND_ENV"
    for command_name in git curl gzip; do
        command -v "$command_name" >/dev/null || {
            printf '%s is required.\n' "$command_name" >&2
            return 1
        }
    done
    PROJECT_DIR="$ROOT_DIR" "$ROOT_DIR/deploy/preflight.sh"
    validate_signed_release_bundle "$bundle_dir" "$SIGNING_KEY"
    cmp -s "$ROOT_DIR/compose.yaml" "$bundle_dir/docker-compose.yml" || {
        echo "Bundled Compose file does not match the release checkout." >&2
        return 1
    }
    assert_deploy_window

    git_sha="$(jq -r '.git_sha' "$bundle_dir/manifest.json")"
    head_sha="$(git -C "$ROOT_DIR" rev-parse HEAD)"
    remote_sha="$(git -C "$ROOT_DIR" rev-parse origin/main 2>/dev/null || true)"
    [[ "$git_sha" == "$head_sha" && "$git_sha" == "$remote_sha" ]] || {
        echo "Bundle, checkout, and origin/main do not identify the same commit." >&2
        return 1
    }
    if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=no)" ]]; then
        echo "Tracked files are modified; refusing deployment." >&2
        return 1
    fi

    mapfile -d '' -t DOCKER < <(docker_engine_command)
    "${DOCKER[@]}" compose version >/dev/null
    if APP_VERSION="$(env_value "$BACKEND_ENV" APP_VERSION)" "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" -f "$bundle_dir/docker-compose.yml" \
        --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" ps -q 2>/dev/null | grep -q .; then
        existing_stack=true
    fi

    if [[ -f "$ACTIVE_DIR/manifest.json" ]]; then
        validate_signed_release_bundle "$ACTIVE_DIR" "$SIGNING_KEY"
        old_version="$(release_bundle_version "$ACTIVE_DIR")"
    fi
    if [[ "$existing_stack" == true && -z "$old_version" ]]; then
        echo "A legacy stack is running without a verified rollback bundle." >&2
        echo "Stop it after capture/EOD completes, then run this as a first bundle deploy." >&2
        return 1
    fi
    if [[ -n "$old_version" || "$existing_stack" == true ]]; then
        capture_check_required=true
    fi
    assert_capture_stopped "$BACKEND_ENV" "$capture_check_required"
    ensure_release_image "$bundle_dir" backend
    ensure_release_image "$bundle_dir" frontend
    assert_capture_stopped "$BACKEND_ENV" "$capture_check_required"
    assert_deploy_window

    version="$(release_bundle_version "$bundle_dir")"
    if [[ -n "$old_version" && "$old_version" != "$version" ]]; then
        mkdir -p "$ROLLBACK_DIR"
        snapshot="$(snapshot_active_bundle "$ACTIVE_DIR" "$ROLLBACK_DIR")"
        printf 'Saved rollback snapshot: %s\n' "$snapshot"
    fi
    PREPARED_ACTIVE="$(prepare_release_bundle "$bundle_dir" "$ACTIVE_DIR")"
    validate_signed_release_bundle "$PREPARED_ACTIVE" "$SIGNING_KEY"
    if [[ -n "$old_version" ]]; then
        acquire_maintenance_lease
    fi

    printf 'Deploying DATA_DOWNLOADER release %s...\n' "$version"
    compose_up_bundle "$PREPARED_ACTIVE" "$version"
    if ! health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV"; then
        echo "Release failed health checks." >&2
        if [[ -n "$old_version" ]]; then
            echo "Restoring previous immutable release $old_version." >&2
            ensure_release_image "$ACTIVE_DIR" backend
            ensure_release_image "$ACTIVE_DIR" frontend
            compose_up_bundle "$ACTIVE_DIR" "$old_version"
            health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || true
        else
            compose_down_bundle "$PREPARED_ACTIVE" "$version"
        fi
        release_maintenance_lease || true
        return 1
    fi

    if ! retired_active="$(activate_prepared_bundle "$PREPARED_ACTIVE" "$ACTIVE_DIR")"; then
        echo "Release activation failed; restoring the previous containers." >&2
        if [[ -n "$old_version" ]]; then
            compose_up_bundle "$ACTIVE_DIR" "$old_version"
            health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || true
        else
            compose_down_bundle "$PREPARED_ACTIVE" "$version"
        fi
        release_maintenance_lease || true
        return 1
    fi
    PREPARED_ACTIVE=""
    set_env_value "$BACKEND_ENV" APP_VERSION "$version"
    write_deployment_pointer "$version"
    release_maintenance_lease
    [[ -z "$retired_active" ]] || rm -rf -- "$retired_active"
    APP_VERSION="$version" "${DOCKER[@]}" compose \
        --project-directory "$ROOT_DIR" -f "$ACTIVE_DIR/docker-compose.yml" \
        --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" ps
    printf 'Deployed immutable release %s. Environment and data files were preserved.\n' "$version"
}

release_config() {
    local key=$1 value=""
    value="${!key:-}"
    if [[ -z "$value" && -f "$RELEASE_ENV" ]]; then
        value="$(env_value "$RELEASE_ENV" "$key")"
    fi
    printf '%s' "$value"
}

ship_bundle() {
    local bundle_dir=$1 ssh_user ssh_host project_dir release_id git_sha remote_sha remote remote_archive
    local archive
    validate_release_bundle "$bundle_dir"
    for command_name in git tar scp ssh; do
        command -v "$command_name" >/dev/null || {
            printf '%s is required.\n' "$command_name" >&2
            return 1
        }
    done
    [[ -f "$SHIP_KEY" ]] || { echo "SSH private key is missing: $SHIP_KEY" >&2; return 1; }
    ssh_user="$(release_config VPS_SSH_USER)"
    ssh_host="$(release_config VPS_SSH_HOST)"
    project_dir="$(release_config VPS_PROJECT_DIR)"
    [[ "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || {
        echo "VPS_SSH_USER is missing or invalid." >&2; return 1;
    }
    validate_tailscale_ipv4 "$ssh_host" || {
        echo "VPS_SSH_HOST must be a Tailscale IPv4 address in 100.64.0.0/10." >&2
        return 1
    }
    [[ "$project_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$project_dir" != *".."* ]] || {
        echo "VPS_PROJECT_DIR must be a safe absolute path." >&2; return 1;
    }

    release_id="$(release_bundle_version "$bundle_dir")"
    git_sha="$(jq -r '.git_sha' "$bundle_dir/manifest.json")"
    git -C "$ROOT_DIR" fetch origin main --quiet
    remote_sha="$(git -C "$ROOT_DIR" rev-parse origin/main)"
    [[ "$git_sha" == "$remote_sha" ]] || {
        echo "Ship gate refused a bundle that is not the current origin/main." >&2
        return 1
    }
    remote="${ssh_user}@${ssh_host}"
    remote_archive="$project_dir/release_manager/.incoming-${release_id}.tar.gz"
    SHIP_ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/market-data-dwndr.${release_id}.XXXXXX.tar.gz")"
    tar -C "$bundle_dir" -czf "$SHIP_ARCHIVE" \
        docker-compose.yml manifest.json manifest.sig version.json \
        images/backend.tar.gz images/frontend.tar.gz
    scp -i "$SHIP_KEY" -o IdentitiesOnly=yes "$SHIP_ARCHIVE" "$remote:$remote_archive"
    ssh -i "$SHIP_KEY" -o IdentitiesOnly=yes "$remote" \
        bash -s -- "$project_dir" "$release_id" "$git_sha" "$remote_archive" <<'REMOTE'
set -euo pipefail
project_dir=$1
release_id=$2
git_sha=$3
remote_archive=$4
incoming_dir="$project_dir/release_manager/recent_builds/.incoming-$release_id"
cleanup() {
    rm -f "$remote_archive"
    rm -rf "$incoming_dir"
}
trap cleanup EXIT
[[ -d "$project_dir/.git" ]] || { echo "VPS project checkout is missing: $project_dir" >&2; exit 1; }
[[ "$(git -C "$project_dir" rev-parse HEAD)" == "$git_sha" ]] || {
    echo "VPS checkout does not match the release commit; git pull --ff-only first." >&2
    exit 1
}
[[ -x "$project_dir/release_manager/deploy.sh" ]] || {
    echo "VPS checkout does not contain the release manager." >&2
    exit 1
}
mkdir -p "$incoming_dir"
tar --no-same-owner --no-same-permissions -xzf "$remote_archive" -C "$incoming_dir"
"$project_dir/release_manager/deploy.sh" --bundle "$incoming_dir"
REMOTE
    rm -f "$SHIP_ARCHIVE"
    SHIP_ARCHIVE=""
    printf 'Shipped and deployed release %s on %s.\n' "$release_id" "$remote"
}

resolve_bundle
acquire_release_lock "$(global_release_lock_file)"
SIGNING_KEY="$(release_key_path "$RELEASE_ENV" RELEASE_SIGNING_PUBLIC_KEY)"
validate_signed_release_bundle "$BUNDLE_DIR" "$SIGNING_KEY"
validate_image_archive_tag "$BUNDLE_DIR/images/backend.tar.gz" \
    "$(jq -r '.images.backend.tag' "$BUNDLE_DIR/manifest.json")"
validate_image_archive_tag "$BUNDLE_DIR/images/frontend.tar.gz" \
    "$(jq -r '.images.frontend.tag' "$BUNDLE_DIR/manifest.json")"
if [[ -n "$SHIP_KEY" ]]; then
    ship_bundle "$BUNDLE_DIR"
else
    deploy_local_bundle "$BUNDLE_DIR"
fi
