#!/usr/bin/env bash
# DATA_DOWNLOADER/rollback.sh — self-contained VPS rollback runner.
#
# Restores a previous release whose images were saved under ROLLBACK_IMAGE_PATH.
# Runs on the VPS from inside the DATA_DOWNLOADER folder. Your `.env` and data
# bind-mounts are never touched.
#
# Usage:
#   ./rollback.sh                 # interactive picker (lists every saved release)
#   ./rollback.sh <release_id>    # restore a specific saved release
#   ./rollback.sh --latest        # restore the newest saved release below the current one
#   ./rollback.sh --list          # show what is saved and exit
#   ./rollback.sh -y <id>         # skip the confirmation prompt
#
# TWO THINGS THIS SCRIPT DOES THAT ARE EASY TO GET WRONG
#
# 1. VERSION-AWARE ORDERING. Release ids sort badly as plain text: `sort -r` puts
#    v0.1.7 above v0.1.31 because "7" > "3" character-wise. A bare rollback would
#    then silently jump back many releases while reporting it as the newest one.
#    Every ordering here uses `sort -V`.
#
# 2. SAVES THE OUTGOING IMAGES FIRST. deploy.sh archives the version it REPLACES,
#    never the one it installs — so the currently running release is usually absent
#    from the store. Without saving it here, rolling back and then pruning loses it
#    for good and there is no way forward again. This saves before switching.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
ENV_FILE="$HERE/.env"
COMPOSE_FILE="$HERE/docker-compose.yml"
TARGET=""
MODE="interactive"   # interactive | latest | explicit | list
ASSUME_YES=false

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --latest)    MODE="latest"; shift ;;
        --list|-l)   MODE="list"; shift ;;
        --yes|-y)    ASSUME_YES=true; shift ;;
        --help|-h)
            sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        -*)          die "unknown option: $1" ;;
        *)
            [[ -z "$TARGET" ]] || die "unexpected argument: $1"
            TARGET="$1"; MODE="explicit"; shift ;;
    esac
done

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

# --------------------------------------------------------------------------- #
# Saved-release inventory
# --------------------------------------------------------------------------- #

# Only releases with BOTH image tarballs are offered: a half-written save (killed
# mid-`docker image save`) would otherwise appear selectable and fail after the
# stack had already been taken down.
list_saved() {
    local dir version
    while IFS= read -r dir; do
        version="$(basename "$dir")"
        [[ -f "$dir/backend.tar.gz" && -f "$dir/frontend.tar.gz" ]] || continue
        printf '%s\n' "$version"
    done < <(find "$rollback_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null) \
        | sort -V -r
}

saved_when() { date -r "$rollback_root/$1/backend.tar.gz" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '?'; }
saved_size() { du -sh "$rollback_root/$1" 2>/dev/null | cut -f1 || echo '?'; }

print_inventory() {
    local -a versions=(); local v idx=0
    while IFS= read -r v; do versions+=("$v"); done < <(list_saved)
    if [[ ${#versions[@]} -eq 0 ]]; then
        printf 'No saved releases under %s\n' "$rollback_root"
        return
    fi
    printf 'Saved releases under %s\n\n' "$rollback_root"
    printf '  %-4s %-12s %-17s %-7s %s\n' '#' 'release' 'saved' 'size' 'notes'
    for v in "${versions[@]}"; do
        if [[ "$v" == "$current_version" ]]; then
            printf '  %-4s %-12s %-17s %-7s %s\n' '-' "$v" "$(saved_when "$v")" "$(saved_size "$v")" \
                'currently deployed'
        else
            idx=$((idx + 1))
            printf '  %-4s %-12s %-17s %-7s %s\n' "${idx})" "$v" "$(saved_when "$v")" "$(saved_size "$v")" ''
        fi
    done
    printf '\n'
}

# Newest saved release strictly below the current one, version-aware.
newest_other() {
    local v
    while IFS= read -r v; do
        [[ "$v" == "$current_version" ]] && continue
        printf '%s\n' "$v"; return 0
    done < <(list_saved)
    return 1
}

choose_interactively() {
    local -a options=(); local v reply
    while IFS= read -r v; do
        [[ "$v" == "$current_version" ]] && continue
        options+=("$v")
    done < <(list_saved)
    [[ ${#options[@]} -gt 0 ]] || die "no saved release other than the active $current_version"

    print_inventory >&2
    printf 'Currently deployed: %s\n' "$current_version" >&2
    while true; do
        printf 'Select a release to roll back to [1-%d, q to quit]: ' "${#options[@]}" >&2
        IFS= read -r reply < /dev/tty || die "no input available"
        case "$reply" in
            q|Q|quit|exit) printf 'aborted\n' >&2; exit 0 ;;
            *[!0-9]*|'') printf '  enter a number, or q to quit\n' >&2 ;;
            *)
                if (( reply >= 1 && reply <= ${#options[@]} )); then
                    printf '%s\n' "${options[$((reply - 1))]}"; return 0
                fi
                printf '  out of range\n' >&2 ;;
        esac
    done
}

if [[ "$MODE" == "list" ]]; then
    print_inventory
    printf 'Currently deployed: %s\n' "$current_version"
    exit 0
fi

# --------------------------------------------------------------------------- #
# Target resolution
# --------------------------------------------------------------------------- #

case "$MODE" in
    latest)
        TARGET="$(newest_other)" || die "no saved release other than the active $current_version"
        log "newest saved release below $current_version is $TARGET" ;;
    interactive)
        # No TTY means a script or cron is driving this (or deploy.sh's recovery
        # path): fall back to --latest rather than blocking on a prompt forever.
        if [[ -t 0 && -e /dev/tty ]]; then
            TARGET="$(choose_interactively)"
        else
            TARGET="$(newest_other)" || die "no saved release other than the active $current_version"
            log "no TTY; falling back to the newest saved release: $TARGET"
        fi ;;
esac

save_dir="$rollback_root/$TARGET"
[[ -d "$save_dir" ]] || die "release $TARGET is not in the rollback store ($rollback_root)"
[[ -f "$save_dir/backend.tar.gz" && -f "$save_dir/frontend.tar.gz" ]] \
    || die "saved images for $TARGET are incomplete under $save_dir"
[[ "$TARGET" != "$current_version" ]] || die "release $TARGET is already active"

# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #

status="$(curl -fsS --max-time 3 "http://${bind_address}:${backend_port}/api/capture/status" 2>/dev/null || true)"
if [[ -n "$status" ]] && grep -Eq '"running"[[:space:]]*:[[:space:]]*true' <<<"$status"; then
    die "capture is running; wait for market close before rolling back"
fi

if [[ "$ASSUME_YES" != true && -t 0 && -e /dev/tty ]]; then
    printf 'Roll back %s -> %s? This restarts both services. [y/N]: ' "$current_version" "$TARGET" >&2
    IFS= read -r confirm < /dev/tty || confirm=""
    [[ "$confirm" == y || "$confirm" == Y ]] || { printf 'aborted\n' >&2; exit 0; }
fi

# --------------------------------------------------------------------------- #
# Preserve the outgoing release, then switch
# --------------------------------------------------------------------------- #

compose() { APP_VERSION="$1" "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${@:2}"; }
wait_http() { local u=$1 l=$2 i; for i in $(seq 1 30); do curl -fsS --max-time 3 "$u" >/dev/null 2>&1 && { log "$l healthy"; return 0; }; sleep 2; done; printf '%s health failed: %s\n' "$l" "$u" >&2; return 1; }

# deploy.sh only archives the release it replaces, so the running one is typically
# NOT in the store. Save it before switching, otherwise this rollback plus any
# later image prune destroys the only copy and there is no route forward again.
outgoing_dir="$rollback_root/$current_version"
if [[ -f "$outgoing_dir/backend.tar.gz" && -f "$outgoing_dir/frontend.tar.gz" ]]; then
    log "outgoing $current_version already saved in the rollback store"
else
    missing=false
    for svc in backend frontend; do
        "${DOCKER[@]}" image inspect "market-data-dwndr-${svc}:${current_version}" >/dev/null 2>&1 \
            || { printf 'warning: image market-data-dwndr-%s:%s not present; cannot archive it\n' \
                    "$svc" "$current_version" >&2; missing=true; }
    done
    if [[ "$missing" == true ]]; then
        printf 'warning: rolling back without a complete archive of %s\n' "$current_version" >&2
    else
        log "saving outgoing images for $current_version to $outgoing_dir"
        mkdir -p "$outgoing_dir"
        # Write to temporary names first: a save interrupted half-way would otherwise
        # leave a tarball that looks complete to list_saved.
        "${DOCKER[@]}" image save "market-data-dwndr-backend:${current_version}" \
            | gzip -n > "$outgoing_dir/backend.tar.gz.partial"
        "${DOCKER[@]}" image save "market-data-dwndr-frontend:${current_version}" \
            | gzip -n > "$outgoing_dir/frontend.tar.gz.partial"
        mv "$outgoing_dir/backend.tar.gz.partial" "$outgoing_dir/backend.tar.gz"
        mv "$outgoing_dir/frontend.tar.gz.partial" "$outgoing_dir/frontend.tar.gz"
        printf '%s\n' "$current_version" > "$outgoing_dir/version.txt"
    fi
fi

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
log "$current_version remains in the rollback store, so you can move forward again"
