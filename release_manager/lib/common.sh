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
