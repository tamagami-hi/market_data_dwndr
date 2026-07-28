#!/usr/bin/env bash
# Shared helpers for the build-machine release scripts (export / deploy / rollback
# / status). The self-contained VPS runners under DATA_DOWNLOADER/ deliberately do
# NOT depend on this file.

env_value() {
    local env_file=$1 key=$2
    sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | tr -d '\r'
}

set_env_value() {
    local env_file=$1 key=$2 value=$3 temp_file
    temp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
    awk -v key="$key" -v value="$value" '
        index($0, key "=") == 1 { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$env_file" > "$temp_file"
    chmod --reference="$env_file" "$temp_file" 2>/dev/null || chmod 600 "$temp_file"
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

# Deterministic identity of the built images (git SHA is combined with this).
image_build_config_hash() {
    local backend_env=$1 frontend_env=$2 backend_url app_name app_uid app_gid
    # An optional 3rd arg overrides the baked NEXT_PUBLIC_BACKEND_URL so the build identity
    # matches the value actually compiled in (same-origin builds force it empty, decoupled
    # from the dev-oriented frontend/.env.local). Without it, fall back to the env file.
    if [[ $# -ge 3 ]]; then
        backend_url=$3
    else
        backend_url="$(env_value "$frontend_env" NEXT_PUBLIC_BACKEND_URL)"
    fi
    app_name="$(env_value "$frontend_env" NEXT_PUBLIC_APP_NAME)"
    [[ -n "$app_name" ]] || app_name="TickVault"
    app_uid="$(env_value "$backend_env" APP_UID)"
    app_gid="$(env_value "$backend_env" APP_GID)"
    # An empty NEXT_PUBLIC_BACKEND_URL is intentional: it selects same-origin mode
    # (relative /api + window.location WS behind a reverse proxy). It still yields a
    # stable, deterministic build identity, so it is allowed here.
    if [[ ! "$app_uid" =~ ^[0-9]+$ || ! "$app_gid" =~ ^[0-9]+$ ]]; then
        echo "APP_UID and APP_GID must be numeric to identify the image build." >&2
        return 1
    fi
    printf 'NEXT_PUBLIC_BACKEND_URL=%s\nNEXT_PUBLIC_APP_NAME=%s\nAPP_UID=%s\nAPP_GID=%s\n' \
        "$backend_url" "$app_name" "$app_uid" "$app_gid" | sha256sum | cut -c1-12
}

# Confirm an archive contains exactly the expected single image tag.
validate_image_archive_tag() {
    local archive=$1 expected_tag=$2
    command -v tar >/dev/null || { echo "tar is required." >&2; return 1; }
    tar -xOzf "$archive" manifest.json 2>/dev/null | jq -e --arg expected "$expected_tag" '
        type == "array" and length == 1 and ([.[].RepoTags[]?] == [$expected])
    ' >/dev/null 2>&1 || {
        echo "Image archive does not contain exactly the expected tag: $expected_tag" >&2
        return 1
    }
}

# Verify a bundle's compose + image archives against the sha256 in its manifest.
# Which services a bundle actually ships. A PARTIAL bundle (built with export.sh
# --frontend / --backend) contains only the selected image archives, so every consumer
# must read this list rather than assuming both are present. Falls back to both for
# bundles produced before partial exports existed.
bundle_services() {
    local bundle_dir=$1 services
    services="$(jq -r '(.services // ["backend","frontend"]) | .[]' \
        "$bundle_dir/manifest.json" 2>/dev/null)" || return 1
    [[ -n "$services" ]] || { echo "backend"; echo "frontend"; return; }
    printf '%s\n' "$services"
}

verify_bundle_sha256() {
    local bundle_dir=$1 rel expected actual service
    for pair in "docker-compose.yml:.compose.sha256"; do
        rel="${pair%%:*}"
        expected="$(jq -r "${pair#*:}" "$bundle_dir/manifest.json")"
        [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo "Missing checksum for $rel." >&2; return 1; }
        [[ -f "$bundle_dir/$rel" ]] || { echo "Missing bundle artifact: $rel." >&2; return 1; }
        actual="$(sha256sum "$bundle_dir/$rel" | cut -d' ' -f1)"
        [[ "$actual" == "$expected" ]] || { echo "Checksum mismatch: $rel." >&2; return 1; }
    done
    # Only the services this bundle ships are expected on disk.
    while read -r service; do
        [[ -n "$service" ]] || continue
        rel="images/${service}.tar.gz"
        expected="$(jq -r ".images.${service}.sha256" "$bundle_dir/manifest.json")"
        [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo "Missing checksum for $rel." >&2; return 1; }
        [[ -f "$bundle_dir/$rel" ]] || { echo "Missing bundle artifact: $rel." >&2; return 1; }
        actual="$(sha256sum "$bundle_dir/$rel" | cut -d' ' -f1)"
        [[ "$actual" == "$expected" ]] || { echo "Checksum mismatch: $rel." >&2; return 1; }
    done < <(bundle_services "$bundle_dir")
}

docker_engine_command() {
    if docker info >/dev/null 2>&1; then
        printf 'docker\0'; return
    fi
    command -v sudo >/dev/null || { echo "Docker access requires sudo, but sudo is unavailable." >&2; exit 1; }
    sudo -v
    printf 'sudo\0docker\0'
}

acquire_release_lock() {
    local lock_file=$1
    command -v flock >/dev/null || { echo "flock is required." >&2; return 1; }
    mkdir -p "$(dirname "$lock_file")" 2>/dev/null || true
    exec {RELEASE_LOCK_FD}>"$lock_file"
    flock -n "$RELEASE_LOCK_FD" || { echo "Another release operation is already running." >&2; return 1; }
}

global_release_lock_file() {
    printf '%s' "${TMPDIR:-/tmp}/market-data-dwndr-release.lock"
}

assert_outside_capture_window() {
    local market_open=$1 market_close=$2 timezone_name=$3 now_hhmm open_minutes close_minutes now_minutes
    [[ "$market_open" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ && "$market_close" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
        echo "MARKET_OPEN and MARKET_CLOSE must use HH:MM." >&2; return 1;
    }
    now_hhmm="${RELEASE_TEST_HHMM:-$(TZ="$timezone_name" date +%H:%M)}"
    open_minutes=$((10#${market_open%:*} * 60 + 10#${market_open#*:}))
    close_minutes=$((10#${market_close%:*} * 60 + 10#${market_close#*:}))
    now_minutes=$((10#${now_hhmm%:*} * 60 + 10#${now_hhmm#*:}))
    if (( open_minutes < close_minutes )); then
        (( now_minutes >= open_minutes && now_minutes < close_minutes )) \
            && { echo "Deployment is blocked during the capture window." >&2; return 1; }
    else
        (( now_minutes >= open_minutes || now_minutes < close_minutes )) \
            && { echo "Deployment is blocked during the capture window." >&2; return 1; }
    fi
    return 0
}

wait_for_http() {
    local url=$1 label=$2 attempt
    for attempt in $(seq 1 30); do
        curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && { printf '%s healthy: %s\n' "$label" "$url"; return 0; }
        sleep 2
    done
    printf '%s health check failed: %s\n' "$label" "$url" >&2; return 1
}

health_check_stack() {
    # Optional 3rd arg: space-separated services to check ("backend frontend" default).
    # A frontend-only deploy must not fail because the backend is mid-capture, and a
    # backend-only deploy should not wait on a frontend it never touched.
    local backend_env=$1 frontend_env=$2 services=${3:-"backend frontend"}
    local bind_address backend_port frontend_port
    bind_address="$(env_value "$backend_env" HOST_BIND_ADDRESS)"
    backend_port="$(env_value "$backend_env" HTTP_PORT)"
    frontend_port="$(env_value "$frontend_env" PORT)"
    [[ "$bind_address" == "0.0.0.0" || -z "$bind_address" ]] && bind_address=127.0.0.1
    if [[ " $services " == *" backend "* ]]; then
        wait_for_http "http://${bind_address}:${backend_port}/health" "Backend" || return 1
    fi
    if [[ " $services " == *" frontend "* ]]; then
        wait_for_http "http://${bind_address}:${frontend_port}/login" "Frontend" || return 1
    fi
}

assert_capture_stopped() {
    local backend_env=$1 require_reachable=${2:-true} bind_address backend_port status
    bind_address="$(env_value "$backend_env" HOST_BIND_ADDRESS)"
    backend_port="$(env_value "$backend_env" HTTP_PORT)"
    [[ "$bind_address" == "0.0.0.0" || -z "$bind_address" ]] && bind_address=127.0.0.1
    if ! status="$(curl -fsS --max-time 3 "http://${bind_address}:${backend_port}/api/capture/status" 2>/dev/null)"; then
        [[ "$require_reachable" == "true" ]] && { echo "Cannot verify capture state; refusing to restart." >&2; return 1; }
        return 0
    fi
    grep -Eq '"running"[[:space:]]*:[[:space:]]*true' <<<"$status" \
        && { echo "Refusing to restart while capture is running." >&2; return 1; }
    grep -Eq '"running"[[:space:]]*:[[:space:]]*false' <<<"$status" \
        || { echo "Capture status response is malformed; refusing to restart." >&2; return 1; }
    return 0
}
