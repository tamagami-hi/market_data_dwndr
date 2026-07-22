#!/usr/bin/env bash

release_root() {
    cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

env_value() {
    local env_file=$1 key=$2
    sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | tr -d '\r'
}

set_env_value() {
    local env_file=$1 key=$2 value=$3 temp_file
    temp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$env_file" > "$temp_file"
    chmod --reference="$env_file" "$temp_file"
    mv "$temp_file" "$env_file"
}

require_file() {
    local path=$1
    [[ -f "$path" ]] || { printf 'Required file is missing: %s\n' "$path" >&2; exit 1; }
}

release_bundle_version() {
    local bundle_dir=$1 version_file="$1/version.json"
    [[ -f "$version_file" ]] || return 1
    jq -er '.version | select(type == "string" and length > 0)' "$version_file" 2>/dev/null
}

sign_release_manifest() {
    local bundle_dir=$1 private_key=$2
    validate_release_bundle "$bundle_dir" || return 1
    [[ -f "$private_key" ]] || { echo "Release signing private key is missing." >&2; return 1; }
    command -v openssl >/dev/null || { echo "openssl is required." >&2; return 1; }
    openssl pkeyutl -sign -rawin -inkey "$private_key" \
        -in "$bundle_dir/manifest.json" 2>/dev/null \
        | base64 -w 0 > "$bundle_dir/manifest.sig"
    printf '\n' >> "$bundle_dir/manifest.sig"
}

validate_signed_release_bundle() {
    local bundle_dir=$1 public_key=$2
    validate_release_bundle "$bundle_dir" || return 1
    [[ -f "$bundle_dir/manifest.sig" ]] || {
        echo "Release manifest signature is missing." >&2
        return 1
    }
    [[ -f "$public_key" ]] || { echo "Release signing public key is missing." >&2; return 1; }
    command -v openssl >/dev/null || { echo "openssl is required." >&2; return 1; }
    if ! openssl pkeyutl -verify -pubin -rawin -inkey "$public_key" \
        -in "$bundle_dir/manifest.json" \
        -sigfile <(base64 -d "$bundle_dir/manifest.sig" 2>/dev/null) >/dev/null 2>&1; then
        echo "Release manifest signature verification failed." >&2
        return 1
    fi
}

release_key_path() {
    local release_env=$1 key_name=$2 key_path=""
    key_path="${!key_name:-}"
    if [[ -z "$key_path" && -f "$release_env" ]]; then
        key_path="$(env_value "$release_env" "$key_name")"
    fi
    [[ "$key_path" == /* && -f "$key_path" ]] || {
        printf '%s must point to a readable absolute key file.\n' "$key_name" >&2
        return 1
    }
    printf '%s' "$key_path"
}

validate_image_archive_tag() {
    local archive=$1 expected_tag=$2
    command -v tar >/dev/null || { echo "tar is required." >&2; return 1; }
    tar -xOzf "$archive" manifest.json 2>/dev/null | jq -e --arg expected "$expected_tag" '
        type == "array" and length == 1 and
        ([.[].RepoTags[]?] == [$expected])
    ' >/dev/null 2>&1 || {
        echo "Image archive does not contain exactly the expected tag: $expected_tag" >&2
        return 1
    }
}

assert_outside_capture_window() {
    local market_open=$1 market_close=$2 timezone_name=$3 now_hhmm open_minutes close_minutes
    [[ "$market_open" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ \
        && "$market_close" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
        echo "MARKET_OPEN and MARKET_CLOSE must use HH:MM." >&2
        return 1
    }
    now_hhmm="${RELEASE_TEST_HHMM:-$(TZ="$timezone_name" date +%H:%M)}"
    [[ "$now_hhmm" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || return 1
    open_minutes=$((10#${market_open%:*} * 60 + 10#${market_open#*:}))
    close_minutes=$((10#${market_close%:*} * 60 + 10#${market_close#*:}))
    local now_minutes=$((10#${now_hhmm%:*} * 60 + 10#${now_hhmm#*:}))
    if (( open_minutes < close_minutes )); then
        if (( now_minutes >= open_minutes && now_minutes < close_minutes )); then
            echo "Deployment is blocked during the configured capture window." >&2
            return 1
        fi
    elif (( now_minutes >= open_minutes || now_minutes < close_minutes )); then
        echo "Deployment is blocked during the configured capture window." >&2
        return 1
    fi
}

acquire_release_lock() {
    local lock_file=$1
    command -v flock >/dev/null || { echo "flock is required." >&2; return 1; }
    mkdir -p "$(dirname "$lock_file")"
    exec {RELEASE_LOCK_FD}>"$lock_file"
    flock -n "$RELEASE_LOCK_FD" || {
        echo "Another release operation is already running." >&2
        return 1
    }
}

global_release_lock_file() {
    printf '/run/lock/market-data-dwndr-release.lock'
}

validate_tailscale_ipv4() {
    local address=$1 first second third fourth
    [[ "$address" =~ ^([0-9]{1,3})[.]([0-9]{1,3})[.]([0-9]{1,3})[.]([0-9]{1,3})$ ]] \
        || return 1
    IFS=. read -r first second third fourth <<<"$address"
    (( 10#$first == 100 \
        && 10#$second >= 64 && 10#$second <= 127 \
        && 10#$third <= 255 && 10#$fourth <= 255 ))
}

validate_release_maintenance_ttl() {
    local backend_env=$1 ttl_seconds
    ttl_seconds="$(env_value "$backend_env" RELEASE_MAINTENANCE_TTL_SECONDS)"
    [[ "$ttl_seconds" =~ ^[0-9]+$ ]] || {
        echo "RELEASE_MAINTENANCE_TTL_SECONDS must be an integer." >&2
        return 1
    }
    if (( 10#$ttl_seconds < 600 || 10#$ttl_seconds > 900 )); then
        echo "RELEASE_MAINTENANCE_TTL_SECONDS must be between 600 and 900 for releases." >&2
        return 1
    fi
}

maintenance_lease_id() {
    local response=$1 lease_id
    lease_id="$(jq -er '.lease_id | select(type == "string" and length >= 16)' \
        <<<"$response")" || {
        echo "Maintenance lease response has an invalid lease id." >&2
        return 1
    }
    printf '%s\n' "$lease_id"
}

validate_maintenance_lease_remaining() {
    local response=$1 minimum_remaining_seconds=${2:-300}
    local expires_at expires_epoch now_epoch
    expires_at="$(jq -er '.expires_at | select(type == "string" and length > 0 and length <= 64)' \
        <<<"$response")" || {
        echo "Maintenance lease response has an invalid expiry." >&2
        return 1
    }
    expires_epoch="$(date -u -d "$expires_at" +%s 2>/dev/null)" || {
        echo "Maintenance lease expiry cannot be parsed." >&2
        return 1
    }
    now_epoch="$(date -u +%s)"
    if (( expires_epoch - now_epoch < minimum_remaining_seconds )); then
        echo "Maintenance lease does not have enough time remaining." >&2
        return 1
    fi
}

atomic_exchange_directories() {
    local left_dir=$1 right_dir=$2
    command -v python3 >/dev/null || { echo "python3 is required." >&2; return 1; }
    python3 -c '
import ctypes, os, sys
AT_FDCWD = -100
RENAME_EXCHANGE = 2
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
result = renameat2(AT_FDCWD, os.fsencode(sys.argv[1]), AT_FDCWD, os.fsencode(sys.argv[2]), RENAME_EXCHANGE)
if result != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))
' "$left_dir" "$right_dir"
}

validate_release_bundle() {
    local bundle_dir=$1 manifest version release_id git_sha git_dirty
    local compose_file compose_sha backend_archive backend_sha backend_tag backend_image_id
    local frontend_archive frontend_sha frontend_tag frontend_image_id actual_sha

    command -v jq >/dev/null || { echo "jq is required to validate release metadata." >&2; return 1; }
    command -v sha256sum >/dev/null || {
        echo "sha256sum is required to validate release artifacts." >&2
        return 1
    }
    [[ -d "$bundle_dir" ]] || { echo "Release bundle is missing: $bundle_dir" >&2; return 1; }
    manifest="$bundle_dir/manifest.json"
    [[ -f "$manifest" && -f "$bundle_dir/version.json" ]] || {
        echo "Release bundle metadata is incomplete: $bundle_dir" >&2
        return 1
    }
    if find "$bundle_dir" -type l -print -quit | grep -q .; then
        echo "Release bundles must not contain symbolic links." >&2
        return 1
    fi
    if find "$bundle_dir" -type f \( -name '.env' -o -name '.env.local' -o -name '.env.*' \) \
        -print -quit | grep -q .; then
        echo "Release bundles must not contain environment files." >&2
        return 1
    fi
    local unexpected_file unexpected_dir
    unexpected_file="$(find "$bundle_dir" -type f \
        ! -path "$bundle_dir/manifest.json" \
        ! -path "$bundle_dir/manifest.sig" \
        ! -path "$bundle_dir/version.json" \
        ! -path "$bundle_dir/docker-compose.yml" \
        ! -path "$bundle_dir/images/backend.tar.gz" \
        ! -path "$bundle_dir/images/frontend.tar.gz" \
        ! -path "$bundle_dir/.gitkeep" \
        ! -path "$bundle_dir/images/.gitkeep" \
        -print -quit)"
    if [[ -n "$unexpected_file" ]]; then
        echo "Release bundle contains an unexpected file: $unexpected_file" >&2
        return 1
    fi
    for keep_file in "$bundle_dir/.gitkeep" "$bundle_dir/images/.gitkeep"; do
        if [[ -f "$keep_file" && -s "$keep_file" ]]; then
            echo "Release .gitkeep files must be empty." >&2
            return 1
        fi
    done
    unexpected_dir="$(find "$bundle_dir" -mindepth 1 -type d \
        ! -path "$bundle_dir/images" -print -quit)"
    if [[ -n "$unexpected_dir" ]]; then
        echo "Release bundle contains an unexpected directory: $unexpected_dir" >&2
        return 1
    fi
    jq -e '
        .schema_version == 1 and
        (.release_id | type == "string") and
        (.git_sha | type == "string") and
        .git_dirty == false and
        .compose.file == "docker-compose.yml" and
        .images.backend.archive == "images/backend.tar.gz" and
        .images.frontend.archive == "images/frontend.tar.gz"
    ' "$manifest" >/dev/null 2>&1 || {
        echo "Release manifest schema is invalid." >&2
        return 1
    }

    release_id="$(jq -r '.release_id' "$manifest")"
    git_sha="$(jq -r '.git_sha' "$manifest")"
    git_dirty="$(jq -r '.git_dirty' "$manifest")"
    version="$(release_bundle_version "$bundle_dir")" || {
        echo "Release version metadata is invalid." >&2
        return 1
    }
    [[ "$release_id" =~ ^[0-9a-f]{12}-[0-9a-f]{12}$ ]] || {
        echo "Release identifier is invalid: $release_id" >&2
        return 1
    }
    [[ "$git_sha" =~ ^[0-9a-f]{40}$ && "$git_dirty" == "false" ]] || {
        echo "Release Git provenance is invalid." >&2
        return 1
    }
    [[ "$version" == "$release_id" ]] || {
        echo "version.json and manifest.json disagree." >&2
        return 1
    }

    compose_file="$(jq -r '.compose.file' "$manifest")"
    compose_sha="$(jq -r '.compose.sha256' "$manifest")"
    backend_archive="$(jq -r '.images.backend.archive' "$manifest")"
    backend_sha="$(jq -r '.images.backend.sha256' "$manifest")"
    backend_tag="$(jq -r '.images.backend.tag' "$manifest")"
    backend_image_id="$(jq -r '.images.backend.image_id' "$manifest")"
    frontend_archive="$(jq -r '.images.frontend.archive' "$manifest")"
    frontend_sha="$(jq -r '.images.frontend.sha256' "$manifest")"
    frontend_tag="$(jq -r '.images.frontend.tag' "$manifest")"
    frontend_image_id="$(jq -r '.images.frontend.image_id' "$manifest")"

    [[ "$compose_sha" =~ ^[0-9a-f]{64}$ \
        && "$backend_sha" =~ ^[0-9a-f]{64}$ \
        && "$frontend_sha" =~ ^[0-9a-f]{64}$ ]] || {
        echo "Release artifact checksums are invalid." >&2
        return 1
    }
    [[ "$backend_tag" == "market-data-dwndr-backend:${release_id}" \
        && "$frontend_tag" == "market-data-dwndr-frontend:${release_id}" ]] || {
        echo "Release image tags do not match the immutable release identifier." >&2
        return 1
    }
    [[ "$backend_image_id" =~ ^sha256:[0-9a-f]{64}$ \
        && "$frontend_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
        echo "Release image identifiers are invalid." >&2
        return 1
    }

    for artifact in "$compose_file:$compose_sha" "$backend_archive:$backend_sha" \
        "$frontend_archive:$frontend_sha"; do
        local relative_path=${artifact%%:*} expected_sha=${artifact#*:}
        [[ -f "$bundle_dir/$relative_path" ]] || {
            echo "Release artifact is missing: $relative_path" >&2
            return 1
        }
        actual_sha="$(sha256sum "$bundle_dir/$relative_path" | cut -d' ' -f1)"
        [[ "$actual_sha" == "$expected_sha" ]] || {
            echo "Release artifact checksum failed: $relative_path" >&2
            return 1
        }
    done
}

prepare_release_bundle() {
    local source_dir=$1 destination_dir=$2 parent_dir stage_dir
    validate_release_bundle "$source_dir" || return 1
    parent_dir="$(dirname "$destination_dir")"
    mkdir -p "$parent_dir"
    stage_dir="$(mktemp -d "$parent_dir/.bundle-stage.XXXXXX")"
    mkdir -p "$stage_dir/images"
    if ! cp "$source_dir/docker-compose.yml" "$stage_dir/docker-compose.yml" \
        || ! cp "$source_dir/manifest.json" "$stage_dir/manifest.json" \
        || ! cp "$source_dir/version.json" "$stage_dir/version.json" \
        || ! cp "$source_dir/images/backend.tar.gz" "$stage_dir/images/backend.tar.gz" \
        || ! cp "$source_dir/images/frontend.tar.gz" "$stage_dir/images/frontend.tar.gz"; then
        rm -rf -- "$stage_dir"
        return 1
    fi
    if [[ -f "$source_dir/manifest.sig" ]] \
        && ! cp "$source_dir/manifest.sig" "$stage_dir/manifest.sig"; then
        rm -rf -- "$stage_dir"
        return 1
    fi
    if [[ -f "$destination_dir/.gitkeep" ]] \
        && ! cp "$destination_dir/.gitkeep" "$stage_dir/.gitkeep"; then
        rm -rf -- "$stage_dir"
        return 1
    fi
    if [[ -f "$destination_dir/images/.gitkeep" ]]; then
        if ! cp "$destination_dir/images/.gitkeep" "$stage_dir/images/.gitkeep"; then
            rm -rf -- "$stage_dir"
            return 1
        fi
    fi
    if ! validate_release_bundle "$stage_dir"; then
        rm -rf -- "$stage_dir"
        return 1
    fi
    printf '%s\n' "$stage_dir"
}

activate_prepared_bundle() {
    local stage_dir=$1 destination_dir=$2
    validate_release_bundle "$stage_dir" || return 1
    if [[ -e "$destination_dir" ]]; then
        if ! atomic_exchange_directories "$stage_dir" "$destination_dir"; then
            return 1
        fi
        printf '%s\n' "$stage_dir"
        return 0
    fi
    if ! mv "$stage_dir" "$destination_dir"; then
        return 1
    fi
}

copy_release_bundle() {
    local source_dir=$1 destination_dir=$2 stage_dir retired_dir
    validate_release_bundle "$source_dir" || return 1
    if [[ "$(realpath -m "$source_dir")" == "$(realpath -m "$destination_dir")" ]]; then
        return 0
    fi
    stage_dir="$(prepare_release_bundle "$source_dir" "$destination_dir")" || return 1
    if ! retired_dir="$(activate_prepared_bundle "$stage_dir" "$destination_dir")"; then
        rm -rf -- "$stage_dir"
        return 1
    fi
    [[ -z "$retired_dir" ]] || rm -rf -- "$retired_dir"
}

snapshot_active_bundle() {
    local active_dir=$1 rollback_dir=$2 version stamp snapshot_dir
    [[ -f "$active_dir/manifest.json" ]] || return 0
    validate_release_bundle "$active_dir" || {
        echo "Active release metadata is invalid; refusing to create a rollback snapshot." >&2
        return 1
    }
    version="$(release_bundle_version "$active_dir")"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    snapshot_dir="$rollback_dir/${stamp}-${version}"
    if [[ -e "$snapshot_dir" ]]; then
        echo "Rollback snapshot already exists: $snapshot_dir" >&2
        return 1
    fi
    if ! copy_release_bundle "$active_dir" "$snapshot_dir"; then
        rm -rf "$snapshot_dir"
        return 1
    fi
    printf '%s\n' "$snapshot_dir"
}

image_build_config_hash() {
    local backend_env=$1 frontend_env=$2 backend_url app_uid app_gid
    backend_url="$(env_value "$frontend_env" NEXT_PUBLIC_BACKEND_URL)"
    app_uid="$(env_value "$backend_env" APP_UID)"
    app_gid="$(env_value "$backend_env" APP_GID)"
    if [[ -z "$backend_url" ]]; then
        echo "NEXT_PUBLIC_BACKEND_URL is required to identify the frontend build." >&2
        return 1
    fi
    if [[ ! "$app_uid" =~ ^[0-9]+$ || ! "$app_gid" =~ ^[0-9]+$ ]]; then
        echo "APP_UID and APP_GID must be numeric to identify the image build." >&2
        return 1
    fi
    printf 'NEXT_PUBLIC_BACKEND_URL=%s\nAPP_UID=%s\nAPP_GID=%s\n' \
        "$backend_url" "$app_uid" "$app_gid" | sha256sum | cut -c1-12
}

docker_engine_command() {
    if docker info >/dev/null 2>&1; then
        printf 'docker\0'
        return
    fi
    command -v sudo >/dev/null || {
        echo "Docker access requires sudo, but sudo is unavailable." >&2
        exit 1
    }
    sudo -v
    printf 'sudo\0docker\0'
}

wait_for_http() {
    local url=$1 label=$2 attempt
    for attempt in $(seq 1 30); do
        if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
            printf '%s healthy: %s\n' "$label" "$url"
            return 0
        fi
        sleep 2
    done
    printf '%s health check failed: %s\n' "$label" "$url" >&2
    return 1
}

health_check_stack() {
    local backend_env=$1 frontend_env=$2 bind_address backend_port frontend_port
    bind_address="$(env_value "$backend_env" HOST_BIND_ADDRESS)"
    backend_port="$(env_value "$backend_env" HTTP_PORT)"
    frontend_port="$(env_value "$frontend_env" PORT)"
    [[ "$bind_address" == "0.0.0.0" ]] && bind_address=127.0.0.1
    wait_for_http "http://${bind_address}:${backend_port}/health" "Backend" || return 1
    wait_for_http "http://${bind_address}:${frontend_port}/login" "Frontend" || return 1
}

assert_capture_stopped() {
    local backend_env=$1 require_reachable=${2:-true} bind_address backend_port status
    bind_address="$(env_value "$backend_env" HOST_BIND_ADDRESS)"
    backend_port="$(env_value "$backend_env" HTTP_PORT)"
    [[ "$bind_address" == "0.0.0.0" ]] && bind_address=127.0.0.1
    if ! status="$(curl -fsS --max-time 3 \
        "http://${bind_address}:${backend_port}/api/capture/status" 2>/dev/null)"; then
        if [[ "$require_reachable" == "true" ]]; then
            echo "Cannot verify capture state on the existing deployment; refusing to restart." >&2
            return 1
        fi
        return 0
    fi
    if printf '%s' "$status" | grep -Eq '"running"[[:space:]]*:[[:space:]]*true'; then
        echo "Refusing to restart while capture is running; stop and verify writer flush first." >&2
        return 1
    fi
    if ! printf '%s' "$status" | grep -Eq '"running"[[:space:]]*:[[:space:]]*false'; then
        echo "Capture status response is malformed; refusing to restart." >&2
        return 1
    fi
}
