#!/usr/bin/env bash

# Read-only release, bundle, deployment, and rollback status.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
RELEASE_ENV="$RELEASE_DIR/.env"
ACTIVE_DIR="$RELEASE_DIR/DATA_DOWNLOADER"
RECENT_DIR="$RELEASE_DIR/recent_builds"
ROLLBACK_DIR="$RELEASE_DIR/rollback"
REMOTE_KEY=""

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remote) REMOTE_KEY="${2:?--remote requires an SSH private key}"; shift 2 ;;
        --local) shift ;;
        --help|-h)
            echo "Usage: ./release_manager/status.sh [--remote SSH_KEY]"
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

bundle_status() {
    local bundle_dir=$1 label=$2 version="unknown" state="invalid"
    local public_key=""
    public_key="$(release_key_path "$RELEASE_ENV" RELEASE_SIGNING_PUBLIC_KEY 2>/dev/null || true)"
    if [[ -n "$public_key" ]] \
        && validate_signed_release_bundle "$bundle_dir" "$public_key" >/dev/null 2>&1; then
        version="$(release_bundle_version "$bundle_dir")"
        state="verified"
    elif validate_release_bundle "$bundle_dir" >/dev/null 2>&1; then
        version="$(release_bundle_version "$bundle_dir")"
        state="signature-unverified"
    fi
    printf '%-18s %s (%s)\n' "$label" "$version" "$state"
}

local_sha="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
remote_sha="$(git -C "$ROOT_DIR" rev-parse --short=12 origin/main 2>/dev/null || echo unknown)"
dirty_count="$(git -C "$ROOT_DIR" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

printf '%-18s %s\n' 'local HEAD' "$local_sha"
printf '%-18s %s\n' 'origin/main' "$remote_sha"
printf '%-18s %s\n' 'dirty files' "$dirty_count"
printf '%-18s %s\n' 'backend env' "$([[ -f "$BACKEND_ENV" ]] && echo preserved || echo missing)"
printf '%-18s %s\n' 'frontend env' "$([[ -f "$FRONTEND_ENV" ]] && echo preserved || echo missing)"

if [[ -f "$ACTIVE_DIR/manifest.json" ]]; then
    bundle_status "$ACTIVE_DIR" 'active release'
else
    printf '%-18s %s\n' 'active release' 'none'
fi

staged_count=0
while IFS= read -r bundle_dir; do
    [[ -n "$bundle_dir" ]] || continue
    staged_count=$((staged_count + 1))
    bundle_status "$bundle_dir" "staged #$staged_count"
done < <(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d \
    ! -name '.export-*' ! -name '.incoming-*' -print 2>/dev/null | sort)
[[ "$staged_count" -gt 0 ]] || printf '%-18s %s\n' 'staged bundles' 'none'

rollback_count="$(find "$ROLLBACK_DIR" -mindepth 1 -maxdepth 1 -type d \
    2>/dev/null | wc -l | tr -d ' ')"
printf '%-18s %s\n' 'rollback bundles' "$rollback_count"

if [[ -n "$REMOTE_KEY" ]]; then
    [[ -f "$REMOTE_KEY" ]] || { echo "SSH private key is missing: $REMOTE_KEY" >&2; exit 1; }
    ssh_user="${VPS_SSH_USER:-}"
    ssh_host="${VPS_SSH_HOST:-}"
    project_dir="${VPS_PROJECT_DIR:-}"
    if [[ -f "$RELEASE_ENV" ]]; then
        [[ -n "$ssh_user" ]] || ssh_user="$(env_value "$RELEASE_ENV" VPS_SSH_USER)"
        [[ -n "$ssh_host" ]] || ssh_host="$(env_value "$RELEASE_ENV" VPS_SSH_HOST)"
        [[ -n "$project_dir" ]] || project_dir="$(env_value "$RELEASE_ENV" VPS_PROJECT_DIR)"
    fi
    if [[ ! "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ \
        || ! "$project_dir" =~ ^/[A-Za-z0-9_./-]+$ \
        || "$project_dir" == *".."* ]] || ! validate_tailscale_ipv4 "$ssh_host"; then
        echo "Remote release configuration is missing or invalid." >&2
        exit 1
    fi
    printf '\nRemote status (%s@%s):\n' "$ssh_user" "$ssh_host"
    ssh -i "$REMOTE_KEY" -o IdentitiesOnly=yes "$ssh_user@$ssh_host" \
        "$project_dir/release_manager/status.sh" --local
fi
