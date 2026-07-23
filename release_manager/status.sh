#!/usr/bin/env bash
# Read-only status of the build machine and (optionally) the VPS deployment.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RELEASE_ENV="$RELEASE_DIR/.env"
RECENT_DIR="$RELEASE_DIR/recent_builds"
REMOTE_KEY=""

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remote) REMOTE_KEY="${2:?--remote requires an SSH key}"; shift 2 ;;
        --help|-h) echo "Usage: ./release_manager/status.sh [--remote SSH_KEY]"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

local_sha="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
dirty_count="$(git -C "$ROOT_DIR" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
printf '%-18s %s\n' 'local HEAD' "$local_sha"
printf '%-18s %s\n' 'dirty files' "$dirty_count"

staged=""
while IFS= read -r d; do [[ -n "$d" ]] && staged="$(release_bundle_version "$d" 2>/dev/null || echo invalid)"; done \
    < <(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '.export-*' -print 2>/dev/null | sort)
printf '%-18s %s\n' 'staged bundle' "${staged:-none}"

if [[ -n "$REMOTE_KEY" ]]; then
    [[ -f "$REMOTE_KEY" ]] || { echo "SSH key is missing: $REMOTE_KEY" >&2; exit 1; }
    release_config() { local k=$1 v="${!1:-}"; [[ -n "$v" || ! -f "$RELEASE_ENV" ]] || v="$(env_value "$RELEASE_ENV" "$k")"; printf '%s' "$v"; }
    ssh_user="$(release_config VPS_SSH_USER)"; ssh_host="$(release_config VPS_SSH_HOST)"; deploy_dir="$(release_config VPS_DEPLOY_DIR)"
    [[ "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ && -n "$ssh_host" && "$deploy_dir" =~ ^/[A-Za-z0-9_./-]+$ ]] \
        || { echo "Remote config missing/invalid in release_manager/.env." >&2; exit 1; }
    printf '\nRemote (%s@%s:%s):\n' "$ssh_user" "$ssh_host" "$deploy_dir"
    ssh -i "$REMOTE_KEY" -o IdentitiesOnly=yes "${ssh_user}@${ssh_host}" bash -s -- "$deploy_dir" <<'REMOTE'
set -euo pipefail
dir=$1
cd "$dir" 2>/dev/null || { echo "  not deployed yet"; exit 0; }
active="$(sed -n 's/^APP_VERSION=//p' .env 2>/dev/null | tail -n1 | tr -d '\r')"
printf '  %-16s %s\n' 'active release' "${active:-unknown}"
printf '  %-16s %s\n' 'env present' "$([[ -f .env ]] && echo yes || echo no)"
D=(docker); docker info >/dev/null 2>&1 || D=(sudo docker)
"${D[@]}" compose --env-file .env -f docker-compose.yml ps 2>/dev/null | sed 's/^/  /' || true
rb="$(sed -n 's/^ROLLBACK_IMAGE_PATH=//p' .env 2>/dev/null | tail -n1 | tr -d '\r')"
if [[ -n "$rb" && -d "$rb" ]]; then
    printf '  %-16s %s\n' 'saved rollbacks' "$(find "$rb" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
fi
REMOTE
fi
