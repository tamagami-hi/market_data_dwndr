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
# Usage:  ./deploy.sh            (invoked automatically by the build machine's ship)
#         ./deploy.sh --frontend Recreate ONLY the frontend (with --no-deps). It cannot
#                                touch capture, so the market-window gate, the
#                                capture-stopped guard and the writer-drain lease are
#                                all skipped — safe to run during market hours.
#         ./deploy.sh --backend  Recreate ONLY the backend. This DOES interrupt capture,
#                                so the guards apply; add --force for an urgent fix
#                                inside market hours.
#         ./deploy.sh --force    Force update: bypass the market-window gate and the
#                                "capture is running" guard, and recreate containers
#                                even if the release is already active. Capture writers
#                                are STILL drained via the maintenance lease before the
#                                container swap, and health-check + auto-rollback still
#                                apply. Use when the live stack is stuck and must be
#                                replaced immediately.
#
# With no service flag the scope comes from the bundle's manifest (`services`), so a
# partial bundle built with export.sh --frontend only ever recreates the frontend.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
ENV_FILE="$HERE/.env"
COMPOSE_FILE="$HERE/docker-compose.yml"
MANIFEST="$HERE/manifest.json"
LEASE_ID=""
FORCE=false
want_frontend=false
want_backend=false

USAGE="Usage: ./deploy.sh [--frontend|--backend] [--force]"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --frontend) want_frontend=true; shift ;;
        --backend) want_backend=true; shift ;;
        --help|-h) echo "$USAGE"; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; echo "$USAGE" >&2; exit 1 ;;
    esac
done

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

for f in "$ENV_FILE" "$COMPOSE_FILE" "$MANIFEST"; do
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
[[ "$release_id" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid release_id in manifest"

# ---- integrity: sha256 of compose + image archives must match the manifest ----
verify_sha() {
    local file_path=$1 expected actual
    expected="$(jq -r "$2" "$MANIFEST")"
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "manifest checksum missing for $file_path"
    actual="$(sha256sum "$file_path" | cut -d' ' -f1)"
    [[ "$actual" == "$expected" ]] || die "checksum mismatch for $file_path"
}
verify_sha "$COMPOSE_FILE" '.compose.sha256'

# ---- scope: which services this run recreates ----
# The bundle declares what it ships; a --frontend/--backend flag may narrow it further.
mapfile -t bundle_svcs < <(jq -r '(.services // ["backend","frontend"]) | .[]' "$MANIFEST")
[[ ${#bundle_svcs[@]} -gt 0 ]] || die "manifest lists no services"
SERVICES=()
if [[ "$want_frontend" == false && "$want_backend" == false ]]; then
    SERVICES=("${bundle_svcs[@]}")
else
    for svc in backend frontend; do
        [[ "$svc" == backend && "$want_backend" == true ]] || \
        [[ "$svc" == frontend && "$want_frontend" == true ]] || continue
        [[ " ${bundle_svcs[*]} " == *" $svc "* ]] \
            || die "this bundle does not ship the $svc image (it ships: ${bundle_svcs[*]})"
        SERVICES+=("$svc")
    done
fi
PARTIAL=false
[[ ${#SERVICES[@]} -eq 2 ]] || PARTIAL=true
TOUCHES_BACKEND=false
[[ " ${SERVICES[*]} " == *" backend "* ]] && TOUCHES_BACKEND=true
log "deploy scope: ${SERVICES[*]}"

declare -A IMAGE_TAG=()
for svc in "${SERVICES[@]}"; do
    tag="$(jq -r ".images.${svc}.tag" "$MANIFEST")"
    [[ "$tag" == "market-data-dwndr-${svc}:${release_id}" ]] \
        || die "manifest image tag for $svc does not match release_id"
    IMAGE_TAG[$svc]="$tag"
done

# ---- required env ----
for key in APP_UID APP_GID HTTP_PORT PORT HOST_BIND_ADDRESS MARKET_DATA_PATH \
    ARCHIVE_DATA_PATH ROLLBACK_IMAGE_PATH RELEASE_IMAGE_PATH; do
    [[ -n "$(env_get "$key")" ]] || die "$key is not set in $ENV_FILE"
done
bind_address="$(env_get HOST_BIND_ADDRESS)"; [[ "$bind_address" == "0.0.0.0" ]] && bind_address=127.0.0.1
backend_port="$(env_get HTTP_PORT)"
frontend_port="$(env_get PORT)"
rollback_root="$(env_get ROLLBACK_IMAGE_PATH)"
release_img_root="$(env_get RELEASE_IMAGE_PATH)"
[[ -d "$(env_get MARKET_DATA_PATH)" ]] || die "MARKET_DATA_PATH does not exist on the host"
[[ -d "$(env_get ARCHIVE_DATA_PATH)" ]] || die "ARCHIVE_DATA_PATH does not exist on the host"
mkdir -p "$rollback_root" "$release_img_root"

for svc in "${SERVICES[@]}"; do
    [[ -f "$release_img_root/${svc}.tar.gz" ]] || die "missing bundle file: $release_img_root/${svc}.tar.gz"
    verify_sha "$release_img_root/${svc}.tar.gz" ".images.${svc}.sha256"
done

api() { printf 'http://%s:%s%s' "$bind_address" "$backend_port" "$1"; }

# ---- market-window gate: never deploy during capture (unless forced) ----
market_open="$(env_get MARKET_OPEN)"; [[ -n "$market_open" ]] || market_open=09:00
market_close="$(env_get MARKET_CLOSE)"; [[ -n "$market_close" ]] || market_close=15:30
tz="$(env_get TIMEZONE)"; [[ -n "$tz" ]] || tz=Asia/Kolkata
now_min=$((10#$(TZ="$tz" date +%H) * 60 + 10#$(TZ="$tz" date +%M)))
open_min=$((10#${market_open%:*} * 60 + 10#${market_open#*:}))
close_min=$((10#${market_close%:*} * 60 + 10#${market_close#*:}))
if (( now_min >= open_min && now_min < close_min )); then
    if [[ "$TOUCHES_BACKEND" == false ]]; then
        log "frontend-only deploy inside the capture window: the backend container is not touched (--no-deps), so capture continues uninterrupted"
    elif [[ "$FORCE" == true ]]; then
        log "FORCE: deploying inside the capture window (${market_open}-${market_close} ${tz})"
    else
        die "refusing to deploy the backend during the capture window (${market_open}-${market_close} ${tz}); use --frontend for a UI-only release, or --force if this is urgent"
    fi
fi

compose() { APP_VERSION="$1" "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${@:2}"; }

# ---- detect a running stack (this is an update, not a first deploy) ----
current_version="$(env_get APP_VERSION)"
existing=false
if [[ "$current_version" != "local" && -n "$current_version" ]] \
    && compose "$current_version" ps -q 2>/dev/null | grep -q .; then
    existing=true
fi
if [[ "$current_version" == "$release_id" ]]; then
    if [[ "$FORCE" == true ]]; then
        log "FORCE: re-deploying already-active release $release_id"
    else
        log "release $release_id already active"; exit 0
    fi
fi

acquire_lease() {
    local token resp
    token="$(env_get RELEASE_MAINTENANCE_TOKEN)"
    if [[ ! "$token" =~ ^[A-Za-z0-9_-]{32,256}$ ]]; then
        [[ "$FORCE" == true ]] && { log "FORCE: RELEASE_MAINTENANCE_TOKEN invalid; skipping writer drain"; return 1; }
        die "RELEASE_MAINTENANCE_TOKEN must be 32-256 URL-safe chars"
    fi
    if ! resp="$(printf 'header = "X-Release-Maintenance-Token: %s"\n' "$token" \
        | curl -fsS --max-time 15 -X POST --config - "$(api /api/capture/maintenance)")"; then
        [[ "$FORCE" == true ]] && { log "FORCE: could not acquire maintenance lease (backend unresponsive?); proceeding without a clean writer drain"; return 1; }
        die "could not acquire the capture maintenance lease"
    fi
    if ! LEASE_ID="$(jq -er '.lease_id' <<<"$resp")"; then
        [[ "$FORCE" == true ]] && { log "FORCE: invalid maintenance lease response; proceeding"; return 1; }
        die "invalid maintenance lease response"
    fi
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

if [[ "$existing" == true && "$TOUCHES_BACKEND" == false ]]; then
    # Frontend-only: the backend container is never recreated, so capture keeps running
    # and we must NOT take the maintenance lease — acquiring it deliberately STOPS
    # capture, which would defeat the whole point of a UI-only release.
    log "frontend-only: leaving capture running (no maintenance lease, no writer drain)"
elif [[ "$existing" == true ]]; then
    # capture must be stopped before we replace containers (unless forced)
    status="$(curl -fsS --max-time 3 "$(api /api/capture/status)" 2>/dev/null)" \
        || { [[ "$FORCE" == true ]] && log "FORCE: cannot verify capture state; proceeding" \
                || die "cannot verify capture state on the running stack; refusing to restart"; }
    if grep -Eq '"running"[[:space:]]*:[[:space:]]*true' <<<"${status:-}"; then
        if [[ "$FORCE" == true ]]; then
            log "FORCE: capture is running; draining writers via the maintenance lease before swap"
        else
            die "capture is running; wait for the EOD/market close before deploying"
        fi
    fi
    # Drain writers before swapping containers. Under --force this is best-effort:
    # a hung/unresponsive backend won't block the replacement.
    acquire_lease || [[ "$FORCE" == true ]]
fi

# Save the CURRENTLY running images so a rollback works even after pruning. Only the
# services being replaced are saved — the others are still running untouched.
if [[ "$existing" == true ]]; then
    save_dir="$rollback_root/$current_version"
    mkdir -p "$save_dir"
    for svc in "${SERVICES[@]}"; do
        if [[ ! -f "$save_dir/${svc}.tar.gz" ]]; then
            log "saving current $svc image to $save_dir"
            "${DOCKER[@]}" image save "market-data-dwndr-${svc}:${current_version}" \
                | gzip -n > "$save_dir/${svc}.tar.gz"
        fi
    done
    printf '%s\n' "$current_version" > "$save_dir/version.txt"
fi

# ---- load the new images (only the services in scope) ----
log "loading images for $release_id (${SERVICES[*]})"
for svc in "${SERVICES[@]}"; do
    gzip -dc "$release_img_root/${svc}.tar.gz" | "${DOCKER[@]}" image load >/dev/null
done

# compose tags BOTH services with ${APP_VERSION}, so any service we are NOT replacing
# needs its running image re-tagged to the new release id — otherwise a later plain
# `compose up` would reference an image tag that was never built.
if [[ "$PARTIAL" == true && -n "$current_version" && "$current_version" != "$release_id" ]]; then
    for svc in backend frontend; do
        [[ " ${SERVICES[*]} " == *" $svc "* ]] && continue
        if "${DOCKER[@]}" image inspect "market-data-dwndr-${svc}:${current_version}" >/dev/null 2>&1; then
            "${DOCKER[@]}" tag "market-data-dwndr-${svc}:${current_version}" \
                "market-data-dwndr-${svc}:${release_id}"
            log "re-tagged unchanged $svc image $current_version -> $release_id"
        else
            log "warning: no market-data-dwndr-${svc}:${current_version} image to re-tag"
        fi
    done
fi

wait_http() {
    local url=$1 label=$2 i
    for i in $(seq 1 30); do
        curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && { log "$label healthy"; return 0; }
        sleep 2
    done
    printf '%s health check failed: %s\n' "$label" "$url" >&2; return 1
}
health() {
    # Only assert on what this run actually replaced.
    if [[ " ${SERVICES[*]} " == *" backend "* ]]; then
        wait_http "$(api /health)" "backend" || return 1
    fi
    if [[ " ${SERVICES[*]} " == *" frontend "* ]]; then
        wait_http "http://${bind_address}:${frontend_port}/login" "frontend" || return 1
    fi
}

set_env APP_VERSION "$release_id"
log "starting release $release_id (${SERVICES[*]})"
up_extra=(--no-build)
[[ "$FORCE" == true ]] && up_extra+=(--force-recreate)
if [[ "$PARTIAL" == true ]]; then
    # --no-deps is what keeps a frontend-only release from restarting the backend.
    up_extra+=(--no-deps "${SERVICES[@]}")
fi
compose "$release_id" up -d "${up_extra[@]}"

if ! health; then
    printf 'release %s failed health checks\n' "$release_id" >&2
    if [[ "$existing" == true ]]; then
        printf 'restoring previous release %s\n' "$current_version" >&2
        set_env APP_VERSION "$current_version"
        # the previous images may still be resident; reload from the rollback store if not
        for svc in "${SERVICES[@]}"; do
            "${DOCKER[@]}" image inspect "market-data-dwndr-${svc}:${current_version}" >/dev/null 2>&1 \
                || gzip -dc "$rollback_root/$current_version/${svc}.tar.gz" \
                    | "${DOCKER[@]}" image load >/dev/null
        done
        restore_extra=(--no-build)
        [[ "$PARTIAL" == true ]] && restore_extra+=(--no-deps "${SERVICES[@]}")
        compose "$current_version" up -d "${restore_extra[@]}"
        health || true
    else
        compose "$release_id" down || true
    fi
    exit 1
fi

release_lease
compose "$release_id" ps
log "deployed release $release_id — ${SERVICES[*]} (env + data preserved)"
if [[ "$PARTIAL" == true ]]; then
    log "the service(s) not listed above were left running untouched"
fi
