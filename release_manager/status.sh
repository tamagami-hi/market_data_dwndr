#!/usr/bin/env bash

# Read-only deployment status, patterned after the BeOnEdge release console.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_ENV="$ROOT_DIR/backend/.env"

# shellcheck source=lib/common.sh
source "$ROOT_DIR/release_manager/lib/common.sh"

local_sha="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
remote_sha="$(git -C "$ROOT_DIR" rev-parse --short=12 origin/main 2>/dev/null || echo unknown)"
dirty_count="$(git -C "$ROOT_DIR" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
deployed="none"
[[ -f "$BACKEND_ENV" ]] && deployed="$(env_value "$BACKEND_ENV" APP_VERSION)"

printf 'local HEAD      : %s\n' "$local_sha"
printf 'origin/main     : %s\n' "$remote_sha"
printf 'dirty files     : %s\n' "$dirty_count"
printf 'deployed image  : %s\n' "${deployed:-none}"
printf 'rollback entries: %s\n' "$(find "$ROOT_DIR/release_manager/rollback" -maxdepth 1 -type f -name '*.version' 2>/dev/null | wc -l | tr -d ' ')"
