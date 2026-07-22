#!/usr/bin/env bash

# Build the exact origin/main source into immutable Docker images and export a
# checksummed, secret-free DATA_DOWNLOADER release bundle.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RECENT_DIR="$RELEASE_DIR/recent_builds"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
RELEASE_ENV="$RELEASE_DIR/.env"
CANDIDATE_BACKEND_IMAGE=""
CANDIDATE_FRONTEND_IMAGE=""
TEMP_BUNDLE=""
DOCKER=()

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

usage() {
    cat <<'USAGE'
Usage: ./release_manager/export.sh

Builds backend and frontend images from a clean origin/main checkout and writes
a signed, checksummed bundle under release_manager/recent_builds/. Actual .env
and .env.local files are read for build configuration but are never copied.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

cleanup_export() {
    [[ -z "$TEMP_BUNDLE" || ! -d "$TEMP_BUNDLE" ]] || rm -rf -- "$TEMP_BUNDLE"
    if [[ ${#DOCKER[@]} -gt 0 ]]; then
        [[ -z "$CANDIDATE_BACKEND_IMAGE" ]] \
            || "${DOCKER[@]}" image rm "$CANDIDATE_BACKEND_IMAGE" >/dev/null 2>&1 || true
        [[ -z "$CANDIDATE_FRONTEND_IMAGE" ]] \
            || "${DOCKER[@]}" image rm "$CANDIDATE_FRONTEND_IMAGE" >/dev/null 2>&1 || true
    fi
}
trap cleanup_export EXIT

require_file "$BACKEND_ENV"
require_file "$FRONTEND_ENV"
for command_name in git jq sha256sum gzip; do
    command -v "$command_name" >/dev/null || {
        printf '%s is required.\n' "$command_name" >&2
        exit 1
    }
done
acquire_release_lock "$(global_release_lock_file)"
signing_private_key="$(release_key_path "$RELEASE_ENV" RELEASE_SIGNING_PRIVATE_KEY)"
private_key_mode="$(stat -c '%a' "$signing_private_key")"
if (( (8#$private_key_mode & 8#077) != 0 )); then
    echo "Release signing private key must not be accessible by group or others." >&2
    exit 1
fi

if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
    echo "Refusing to export a dirty worktree." >&2
    exit 1
fi
git -C "$ROOT_DIR" fetch origin main --quiet
git_sha="$(git -C "$ROOT_DIR" rev-parse HEAD)"
remote_sha="$(git -C "$ROOT_DIR" rev-parse origin/main)"
[[ "$git_sha" == "$remote_sha" ]] || {
    echo "Refusing to export: HEAD is not the current origin/main." >&2
    exit 1
}

mapfile -d '' -t DOCKER < <(docker_engine_command)
COMPOSE=("${DOCKER[@]}" compose)
"${COMPOSE[@]}" version >/dev/null

build_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
release_id="${git_sha:0:12}-${build_hash}"
backend_image="market-data-dwndr-backend:${release_id}"
frontend_image="market-data-dwndr-frontend:${release_id}"
candidate_version="${release_id}.candidate.$$"
CANDIDATE_BACKEND_IMAGE="market-data-dwndr-backend:${candidate_version}"
CANDIDATE_FRONTEND_IMAGE="market-data-dwndr-frontend:${candidate_version}"
compose_args=(--project-directory "$ROOT_DIR" -f "$ROOT_DIR/compose.yaml"
    --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV")

printf 'Building candidate images for immutable release %s...\n' "$release_id"
APP_VERSION="$candidate_version" "${COMPOSE[@]}" "${compose_args[@]}" build --pull
backend_image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$CANDIDATE_BACKEND_IMAGE")"
frontend_image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$CANDIDATE_FRONTEND_IMAGE")"

promote_candidate() {
    local candidate_tag=$1 final_tag=$2 expected_id=$3 existing_id
    existing_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$final_tag" 2>/dev/null || true)"
    if [[ -n "$existing_id" && "$existing_id" != "$expected_id" ]]; then
        printf 'Immutable tag collision: %s is %s, candidate is %s.\n' \
            "$final_tag" "$existing_id" "$expected_id" >&2
        return 1
    fi
    if [[ -z "$existing_id" ]]; then
        "${DOCKER[@]}" image tag "$candidate_tag" "$final_tag"
    fi
}
promote_candidate "$CANDIDATE_BACKEND_IMAGE" "$backend_image" "$backend_image_id"
promote_candidate "$CANDIDATE_FRONTEND_IMAGE" "$frontend_image" "$frontend_image_id"

mkdir -p "$RECENT_DIR"
TEMP_BUNDLE="$(mktemp -d "$RECENT_DIR/.export-${release_id}.XXXXXX")"
mkdir -p "$TEMP_BUNDLE/images"

printf 'Saving %s...\n' "$backend_image"
"${DOCKER[@]}" image save "$backend_image" | gzip -n -9 > "$TEMP_BUNDLE/images/backend.tar.gz"
printf 'Saving %s...\n' "$frontend_image"
"${DOCKER[@]}" image save "$frontend_image" | gzip -n -9 > "$TEMP_BUNDLE/images/frontend.tar.gz"
cp "$ROOT_DIR/compose.yaml" "$TEMP_BUNDLE/docker-compose.yml"
validate_image_archive_tag "$TEMP_BUNDLE/images/backend.tar.gz" "$backend_image"
validate_image_archive_tag "$TEMP_BUNDLE/images/frontend.tar.gz" "$frontend_image"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backend_sha="$(sha256sum "$TEMP_BUNDLE/images/backend.tar.gz" | cut -d' ' -f1)"
frontend_sha="$(sha256sum "$TEMP_BUNDLE/images/frontend.tar.gz" | cut -d' ' -f1)"
compose_sha="$(sha256sum "$TEMP_BUNDLE/docker-compose.yml" | cut -d' ' -f1)"

jq -n --arg version "$release_id" '{version: $version}' > "$TEMP_BUNDLE/version.json"
jq -n \
    --arg release_id "$release_id" \
    --arg created_at "$created_at" \
    --arg git_sha "$git_sha" \
    --arg git_branch "$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD || echo detached)" \
    --arg build_config_hash "$build_hash" \
    --arg compose_sha "$compose_sha" \
    --arg backend_tag "$backend_image" \
    --arg backend_sha "$backend_sha" \
    --arg backend_image_id "$backend_image_id" \
    --arg frontend_tag "$frontend_image" \
    --arg frontend_sha "$frontend_sha" \
    --arg frontend_image_id "$frontend_image_id" \
    '{
      schema_version: 1,
      project: "market_data_dwndr",
      release_id: $release_id,
      created_at: $created_at,
      git_sha: $git_sha,
      git_branch: $git_branch,
      git_dirty: false,
      build_config_hash: $build_config_hash,
      compose: {file: "docker-compose.yml", sha256: $compose_sha},
      images: {
        backend: {tag: $backend_tag, archive: "images/backend.tar.gz", sha256: $backend_sha, image_id: $backend_image_id},
        frontend: {tag: $frontend_tag, archive: "images/frontend.tar.gz", sha256: $frontend_sha, image_id: $frontend_image_id}
      }
    }' > "$TEMP_BUNDLE/manifest.json"

sign_release_manifest "$TEMP_BUNDLE" "$signing_private_key"
signing_public_key="$(release_key_path "$RELEASE_ENV" RELEASE_SIGNING_PUBLIC_KEY)"
validate_signed_release_bundle "$TEMP_BUNDLE" "$signing_public_key"
bundle_dir="$RECENT_DIR/${release_id}-${stamp}"
[[ ! -e "$bundle_dir" ]] || { echo "Release bundle already exists: $bundle_dir" >&2; exit 1; }
mv "$TEMP_BUNDLE" "$bundle_dir"
TEMP_BUNDLE=""
printf 'Exported immutable release bundle: %s\n' "$bundle_dir"
