#!/usr/bin/env bash
# DATA_DOWNLOADER/deploy.sh — self-contained VPS deploy runner.
#
# Runs ON THE VPS from inside the DATA_DOWNLOADER folder. It has no dependency on
# a git checkout or the build machine. It verifies the bundled images against the
# manifest, gates on the market window, drains capture writers, saves the CURRENT
# images to ROLLBACK_IMAGE_PATH, loads the new images, brings the stack up, and
# health-checks it — rolling back automatically on failure. Your `.env` and the
# data bind-mounts are never touched.
#
# Usage:  ./deploy.sh          (invoked automatically by the build machine's ship)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
ENV_FILE="$HERE/.env"
COMPOSE_FILE="$HERE/docker-compose.yml"
MANIFEST="$HERE/manifest.json"
LEASE_ID=""

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

for f in "$ENV_FILE" "$COMPOSE_FILE" "$MANIFEST" \
    "$HERE/images/backend.tar.gz" "$HERE/images/frontend.tar.gz"; do
    [[ -f "$f" ]] || die "missing bundle file: $f (copy .env.example to .env and fill it)"
done
for cmd in jq sha256sum gzip curl; do
    command -v "$cmd" >/dev/null || die "$cmd is required on the VPS"
done

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'; }
set_env() {
    local key=$1 value=$2 tmp
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    awk -v k="$key" -v v="$value" '
        index($0, k "=") == 1 { print k "=" v; found=1; next }
        { print } END { if (!found) print k "=" v }' "$ENV_FILE" > "$tmp"
    chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
}

# Docker with a sudo fallback (non-interactive-friendly).
DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
"${DOCKER[@]}" compose version >/dev/null || die "docker compose is required"

release_id="$(jq -r '.release_id' "$MANIFEST")"
[[ "$release_id" =~ ^[0-9a-f]{12}-[0-9a-f]{12}$ ]] || die "invalid release_id in manifest"

# ---- integrity: sha256 of compose + image archives must match the manifest ----
verify_sha() {
    local rel=$1 expected actual
    expected="$(jq -r "$2" "$MANIFEST")"
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "manifest checksum missing for $rel"
    actual="$(sha256sum "$HERE/$rel" | cut -d' ' -f1)"
    [[ "$actual" == "$expected" ]] || die "checksum mismatch for $rel"
}
verify_sha "docker-compose.yml" '.compose.sha256'
verify_sha "images/backend.tar.gz" '.images.backend.sha256'
verify_sha "images/frontend.tar.gz" '.images.frontend.sha256'

backend_tag="$(jq -r '.images.backend.tag' "$MANIFEST")"
frontend_tag="$(jq -r '.images.frontend.tag' "$MANIFEST")"
backend_id="$(jq -r '.images.backend.image_id' "$MANIFEST")"
frontend_id="$(jq -r '.images.frontend.image_id' "$MANIFEST")"
[[ "$backend_tag" == "market-data-dwndr-backend:${release_id}" \
    && "$frontend_tag" == "market-data-dwndr-frontend:${release_id}" ]] \
    || die "manifest image tags do not match release_id"

# ---- required env ----
for key in APP_UID APP_GID HTTP_PORT PORT HOST_BIND_ADDRESS MARKET_DATA_PATH \
    ARCHIVE_DATA_PATH ROLLBACK_IMAGE_PATH; do
    [[ -n "$(env_get "$key")" ]] || die "$key is not set in $ENV_FILE"
done
bind_address="$(env_get HOST_BIND_ADDRESS)"; [[ "$bind_address" == "0.0.0.0" ]] && bind_address=127.0.0.1
backend_port="$(env_get HTTP_PORT)"
frontend_port="$(env_get PORT)"
rollback_root="$(env_get ROLLBACK_IMAGE_PATH)"
[[ -d "$(env_get MARKET_DATA_PATH)" ]] || die "MARKET_DATA_PATH does not exist on the host"
[[ -d "$(env_get ARCHIVE_DATA_PATH)" ]] || die "ARCHIVE_DATA_PATH does not exist on the host"
mkdir -p "$rollback_root"

api() { printf 'http://%s:%s%s' "$bind_address" "$backend_port" "$1"; }

# ---- market-window gate: never deploy during capture ----
market_open="$(env_get MARKET_OPEN)"; [[ -n "$market_open" ]] || market_open=09:00
market_close="$(env_get MARKET_CLOSE)"; [[ -n "$market_close" ]] || market_close=15:30
tz="$(env_get TIMEZONE)"; [[ -n "$tz" ]] || tz=Asia/Kolkata
now_min=$((10#$(TZ="$tz" date +%H) * 60 + 10#$(TZ="$tz" date +%M)))
open_min=$((10#${market_open%:*} * 60 + 10#${market_open#*:}))
close_min=$((10#${market_close%:*} * 60 + 10#${market_close#*:}))
if (( now_min >= open_min && now_min < close_min )); then
    die "refusing to deploy during the capture window (${market_open}-${market_close} ${tz})"
fi

compose() { APP_VERSION="$1" "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${@:2}"; }

# ---- detect a running stack (this is an update, not a first deploy) ----
current_version="$(env_get APP_VERSION)"
existing=false
if [[ "$current_version" != "local" && -n "$current_version" ]] \
    && compose "$current_version" ps -q 2>/dev/null | grep -q .; then
    existing=true
fi
[[ "$current_version" != "$release_id" ]] || { log "release $release_id already active"; exit 0; }

acquire_lease() {
    local token resp; token="$(env_get RELEASE_MAINTENANCE_TOKEN)"
    [[ "$token" =~ ^[A-Za-z0-9_-]{32,256}$ ]] || die "RELEASE_MAINTENANCE_TOKEN must be 32-256 URL-safe chars"
    resp="$(printf 'header = "X-Release-Maintenance-Token: %s"\n' "$token" \
        | curl -fsS --max-time 15 -X POST --config - "$(api /api/capture/maintenance)")" \
        || die "could not acquire the capture maintenance lease"
    LEASE_ID="$(jq -er '.lease_id' <<<"$resp")" || die "invalid maintenance lease response"
    log "capture writers drained (lease ${LEASE_ID:0:8}…)"
}
release_lease() {
    [[ -n "$LEASE_ID" ]] || return 0
    local token; token="$(env_get RELEASE_MAINTENANCE_TOKEN)"
    printf 'header = "X-Release-Maintenance-Token: %s"\n' "$token" \
        | curl -fsS --max-time 10 -X DELETE --config - \
            "$(api "/api/capture/maintenance/$LEASE_ID")" >/dev/null 2>&1 || true
    LEASE_ID=""
}
trap release_lease EXIT

if [[ "$existing" == true ]]; then
    # capture must be stopped before we replace containers
    status="$(curl -fsS --max-time 3 "$(api /api/capture/status)" 2>/dev/null)" \
        || die "cannot verify capture state on the running stack; refusing to restart"
    grep -Eq '"running"[[:space:]]*:[[:space:]]*true' <<<"$status" \
        && die "capture is running; wait for the EOD/market close before deploying"
    acquire_lease

    # save the CURRENTLY running images so a rollback works even after pruning
    save_dir="$rollback_root/$current_version"
    if [[ ! -f "$save_dir/backend.tar.gz" || ! -f "$save_dir/frontend.tar.gz" ]]; then
        log "saving current images to $save_dir"
        mkdir -p "$save_dir"
        "${DOCKER[@]}" image save "market-data-dwndr-backend:${current_version}" \
            | gzip -n > "$save_dir/backend.tar.gz"
        "${DOCKER[@]}" image save "market-data-dwndr-frontend:${current_version}" \
            | gzip -n > "$save_dir/frontend.tar.gz"
        printf '%s\n' "$current_version" > "$save_dir/version.txt"
    fi
fi

# ---- load the new images (idempotent: only load if the tag is absent) ----
load_image() {
    local tag=$1 archive=$2 expected_id=$3 have
    have="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag" 2>/dev/null || true)"
    if [[ -z "$have" ]]; then
        gzip -dc "$HERE/$archive" | "${DOCKER[@]}" image load >/dev/null
        have="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$tag")"
    fi
    [[ "$have" == "$expected_id" ]] || die "loaded image identity mismatch for $tag"
}
log "loading images for $release_id"
load_image "$backend_tag" images/backend.tar.gz "$backend_id"
load_image "$frontend_tag" images/frontend.tar.gz "$frontend_id"

wait_http() {
    local url=$1 label=$2 i
    for i in $(seq 1 30); do
        curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && { log "$label healthy"; return 0; }
        sleep 2
    done
    printf '%s health check failed: %s\n' "$label" "$url" >&2; return 1
}
health() {
    wait_http "$(api /health)" "backend" || return 1
    wait_http "http://${bind_address}:${frontend_port}/login" "frontend" || return 1
}

set_env APP_VERSION "$release_id"
log "starting release $release_id"
compose "$release_id" up -d --no-build

if ! health; then
    printf 'release %s failed health checks\n' "$release_id" >&2
    if [[ "$existing" == true ]]; then
        printf 'restoring previous release %s\n' "$current_version" >&2
        set_env APP_VERSION "$current_version"
        # the previous images may still be resident; reload from the rollback store if not
        "${DOCKER[@]}" image inspect "market-data-dwndr-backend:${current_version}" >/dev/null 2>&1 \
            || gzip -dc "$rollback_root/$current_version/backend.tar.gz" | "${DOCKER[@]}" image load >/dev/null
        "${DOCKER[@]}" image inspect "market-data-dwndr-frontend:${current_version}" >/dev/null 2>&1 \
            || gzip -dc "$rollback_root/$current_version/frontend.tar.gz" | "${DOCKER[@]}" image load >/dev/null
        compose "$current_version" up -d --no-build
        health || true
    else
        compose "$release_id" down || true
    fi
    exit 1
fi

release_lease
compose "$release_id" ps
log "deployed release $release_id (env + data preserved)"
