#!/usr/bin/env bash

# Switch the running stack back to an existing immutable image tag. Bind-mounted
# live/archive data and the production env files are never replaced or deleted.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
ROLLBACK_DIR="$ROOT_DIR/release_manager/rollback"
CURRENT_FILE="$ROOT_DIR/release_manager/current-version"

# shellcheck source=lib/common.sh
source "$ROOT_DIR/release_manager/lib/common.sh"

require_file "$BACKEND_ENV"
require_file "$FRONTEND_ENV"
PROJECT_DIR="$ROOT_DIR" "$ROOT_DIR/deploy/preflight.sh"
assert_capture_stopped "$BACKEND_ENV" true
mapfile -d '' -t DOCKER < <(docker_engine_command)
COMPOSE=("${DOCKER[@]}" compose)
compose_args=(--env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV")

target_version=${1:-}
if [[ -z "$target_version" ]]; then
    latest_snapshot="$(find "$ROLLBACK_DIR" -maxdepth 1 -type f -name '*.version' | sort -r | head -n 1)"
    [[ -n "$latest_snapshot" ]] || { echo "No rollback snapshot is available." >&2; exit 1; }
    target_version="$(tr -d '[:space:]' < "$latest_snapshot")"
fi

"${DOCKER[@]}" image inspect "market-data-dwndr-backend:${target_version}" >/dev/null
"${DOCKER[@]}" image inspect "market-data-dwndr-frontend:${target_version}" >/dev/null

current_version="$(env_value "$BACKEND_ENV" APP_VERSION)"
printf 'Rolling back %s -> %s...\n' "${current_version:-unknown}" "$target_version"
assert_capture_stopped "$BACKEND_ENV" true
APP_VERSION="$target_version" "${COMPOSE[@]}" "${compose_args[@]}" up -d --no-build
health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV"
set_env_value "$BACKEND_ENV" APP_VERSION "$target_version"
printf '%s\n' "$target_version" > "$CURRENT_FILE"
printf 'Rollback complete. Data mounts were left unchanged.\n'
