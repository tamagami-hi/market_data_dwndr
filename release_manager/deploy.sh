#!/usr/bin/env bash

# Build and deploy the exact origin/main checkout on this host. The production env
# files are preserved in place and only APP_VERSION is advanced after health checks.

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
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
PROJECT_DIR="$ROOT_DIR" "$ROOT_DIR/deploy/preflight.sh"

mapfile -d '' -t DOCKER < <(docker_engine_command)
COMPOSE=("${DOCKER[@]}" compose)
"${COMPOSE[@]}" version >/dev/null

if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
    echo "Refusing to deploy a dirty worktree. Commit or remove local changes first." >&2
    exit 1
fi

git -C "$ROOT_DIR" fetch origin main --quiet
head_sha="$(git -C "$ROOT_DIR" rev-parse HEAD)"
remote_sha="$(git -C "$ROOT_DIR" rev-parse origin/main)"
if [[ "$head_sha" != "$remote_sha" ]]; then
    echo "Refusing to deploy: HEAD is not the current origin/main." >&2
    exit 1
fi

old_version="$(env_value "$BACKEND_ENV" APP_VERSION)"
is_existing_deployment=false
if [[ -n "$old_version" && "$old_version" != "local" ]]; then
    is_existing_deployment=true
fi
assert_capture_stopped "$BACKEND_ENV" "$is_existing_deployment"

commit_version="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD)"
build_config_version="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
new_version="${commit_version}-${build_config_version}"
mkdir -p "$ROLLBACK_DIR"
if [[ -n "$old_version" && "$old_version" != "local" && "$old_version" != "$new_version" ]]; then
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    printf '%s\n' "$old_version" > "$ROLLBACK_DIR/${timestamp}-${old_version}.version"
fi

compose_args=(--env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV")
backend_image="market-data-dwndr-backend:${new_version}"
frontend_image="market-data-dwndr-frontend:${new_version}"
if "${DOCKER[@]}" image inspect "$backend_image" >/dev/null 2>&1 \
    && "${DOCKER[@]}" image inspect "$frontend_image" >/dev/null 2>&1; then
    printf 'Release %s is already built; reusing its immutable images.\n' "$new_version"
elif "${DOCKER[@]}" image inspect "$backend_image" >/dev/null 2>&1 \
    || "${DOCKER[@]}" image inspect "$frontend_image" >/dev/null 2>&1; then
    echo "Refusing to overwrite a partial release tag; remove the partial images explicitly." >&2
    exit 1
else
    printf 'Building release %s from origin/main...\n' "$new_version"
    APP_VERSION="$new_version" "${COMPOSE[@]}" "${compose_args[@]}" build --pull
fi
# A capture can start during a long image build. Check again at the last possible
# moment before replacing either running container.
assert_capture_stopped "$BACKEND_ENV" "$is_existing_deployment"
APP_VERSION="$new_version" "${COMPOSE[@]}" "${compose_args[@]}" up -d --no-build

if ! health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV"; then
    if ! assert_capture_stopped "$BACKEND_ENV" true; then
        echo "Health checks failed, but capture state is not safe for automatic recovery." >&2
        echo "The failed stack was left running for manual inspection." >&2
        exit 1
    fi
    if [[ -n "$old_version" && "$old_version" != "local" ]]; then
        echo "New release failed health checks; restoring previous images." >&2
        APP_VERSION="$old_version" "${COMPOSE[@]}" "${compose_args[@]}" up -d --no-build
        health_check_stack "$BACKEND_ENV" "$FRONTEND_ENV" || true
    else
        echo "No previous immutable release exists; stopping the failed first deploy." >&2
        APP_VERSION="$new_version" "${COMPOSE[@]}" "${compose_args[@]}" down
    fi
    exit 1
fi

set_env_value "$BACKEND_ENV" APP_VERSION "$new_version"
printf '%s\n' "$new_version" > "$CURRENT_FILE"
"${COMPOSE[@]}" "${compose_args[@]}" ps
printf 'Deployed release %s.\n' "$new_version"
