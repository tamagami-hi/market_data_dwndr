#!/usr/bin/env bash
# DATA_DOWNLOADER/rollback.sh — self-contained VPS rollback runner.
#
# Restores a previous release whose images were saved under ROLLBACK_IMAGE_PATH
# by deploy.sh. Runs on the VPS from inside the DATA_DOWNLOADER folder. Your
# `.env` and data bind-mounts are never touched.
#
# Usage:
#   ./rollback.sh                 # restore the newest saved previous release
#   ./rollback.sh <release_id>    # restore a specific saved release

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
ENV_FILE="$HERE/.env"
COMPOSE_FILE="$HERE/docker-compose.yml"
TARGET="${1:-}"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

for f in "$ENV_FILE" "$COMPOSE_FILE"; do [[ -f "$f" ]] || die "missing: $f"; done
for cmd in gzip curl; do command -v "$cmd" >/dev/null || die "$cmd is required"; done

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'; }
set_env() {
    local key=$1 value=$2 tmp; tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    awk -v k="$key" -v v="$value" 'index($0,k"=")==1{print k"="v;f=1;next}{print}END{if(!f)print k"="v}' \
        "$ENV_FILE" > "$tmp"
    chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

DOCKER=(docker); docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
"${DOCKER[@]}" compose version >/dev/null || die "docker compose is required"

rollback_root="$(env_get ROLLBACK_IMAGE_PATH)"
[[ -d "$rollback_root" ]] || die "ROLLBACK_IMAGE_PATH does not exist: $rollback_root"
current_version="$(env_get APP_VERSION)"
bind_address="$(env_get HOST_BIND_ADDRESS)"; [[ "$bind_address" == "0.0.0.0" ]] && bind_address=127.0.0.1
backend_port="$(env_get HTTP_PORT)"; frontend_port="$(env_get PORT)"

if [[ -z "$TARGET" ]]; then
    TARGET="$(find "$rollback_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
        | grep -v "^${current_version}$" | sort -r | head -n 1)"
    [[ -n "$TARGET" ]] || die "no saved previous release found under $rollback_root"
fi
save_dir="$rollback_root/$TARGET"
[[ -f "$save_dir/backend.tar.gz" && -f "$save_dir/frontend.tar.gz" ]] \
    || die "saved images for $TARGET are missing under $save_dir"
[[ "$TARGET" != "$current_version" ]] || die "release $TARGET is already active"

# capture must be stopped
status="$(curl -fsS --max-time 3 "http://${bind_address}:${backend_port}/api/capture/status" 2>/dev/null || true)"
if [[ -n "$status" ]] && grep -Eq '"running"[[:space:]]*:[[:space:]]*true' <<<"$status"; then
    die "capture is running; wait for market close before rolling back"
fi

compose() { APP_VERSION="$1" "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${@:2}"; }
wait_http() { local u=$1 l=$2 i; for i in $(seq 1 30); do curl -fsS --max-time 3 "$u" >/dev/null 2>&1 && { log "$l healthy"; return 0; }; sleep 2; done; printf '%s health failed: %s\n' "$l" "$u" >&2; return 1; }

log "loading images for $TARGET"
"${DOCKER[@]}" image inspect "market-data-dwndr-backend:${TARGET}" >/dev/null 2>&1 \
    || gzip -dc "$save_dir/backend.tar.gz" | "${DOCKER[@]}" image load >/dev/null
"${DOCKER[@]}" image inspect "market-data-dwndr-frontend:${TARGET}" >/dev/null 2>&1 \
    || gzip -dc "$save_dir/frontend.tar.gz" | "${DOCKER[@]}" image load >/dev/null

set_env APP_VERSION "$TARGET"
log "rolling back $current_version -> $TARGET"
compose "$TARGET" up -d --no-build
if ! { wait_http "http://${bind_address}:${backend_port}/health" backend \
    && wait_http "http://${bind_address}:${frontend_port}/login" frontend; }; then
    printf 'rollback to %s failed health checks\n' "$TARGET" >&2
    set_env APP_VERSION "$current_version"
    exit 1
fi
compose "$TARGET" ps
log "rolled back to $TARGET (env + data preserved)"
