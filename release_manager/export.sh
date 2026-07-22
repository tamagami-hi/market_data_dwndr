#!/usr/bin/env bash
# Build the backend + frontend images from the current checkout and assemble one
# self-contained DATA_DOWNLOADER release bundle under recent_builds/. The bundle
# is everything the VPS needs: images, an image-based compose, the env template,
# the self-contained deploy/rollback runners, and a checksummed manifest.
#
# No secrets are ever copied into the bundle. Typical flow:
#   ./release_manager/export.sh            # build + bundle
#   ./release_manager/deploy.sh            # run locally (compose up here)
#   ./release_manager/deploy.sh --ship KEY # ship the bundle to the VPS

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RECENT_DIR="$RELEASE_DIR/recent_builds"
SRC_DIR="$RELEASE_DIR/DATA_DOWNLOADER"
DEPLOY_COMPOSE="$RELEASE_DIR/compose.deploy.yaml"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
TEMP_BUNDLE=""
DOCKER=()

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

usage() { echo "Usage: ./release_manager/export.sh"; }
BUMP="patch"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --major) BUMP="major"; shift ;;
        --minor) BUMP="minor"; shift ;;
        --patch) BUMP="patch"; shift ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

# Bump version
app_version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT_DIR/backend/app/__init__.py" 2>/dev/null || echo "0.0.0")"
[[ -n "$app_version" ]] || app_version="0.0.0"
IFS=. read -r major minor patch <<<"$app_version"
major="${major:-0}"; minor="${minor:-0}"; patch="${patch:-0}"
case "$BUMP" in
    major) major=$((major + 1)); minor=0; patch=0 ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    patch) patch=$((patch + 1)) ;;
esac
new_version="${major}.${minor}.${patch}"
sed -i "s/^__version__ = .*/__version__ = \"$new_version\"/" "$ROOT_DIR/backend/app/__init__.py"
printf 'Bumped version from %s to %s\n' "$app_version" "$new_version"

cleanup_export() { [[ -z "$TEMP_BUNDLE" || ! -d "$TEMP_BUNDLE" ]] || rm -rf -- "$TEMP_BUNDLE"; }
trap cleanup_export EXIT

require_file "$BACKEND_ENV"
require_file "$FRONTEND_ENV"
require_file "$DEPLOY_COMPOSE"
require_file "$SRC_DIR/.env.example"
require_file "$SRC_DIR/deploy.sh"
require_file "$SRC_DIR/rollback.sh"
require_file "$SRC_DIR/README.md"
for cmd in git jq sha256sum gzip; do
    command -v "$cmd" >/dev/null || { printf '%s is required.\n' "$cmd" >&2; exit 1; }
done
acquire_release_lock "$(global_release_lock_file)"

git_sha="$(git -C "$ROOT_DIR" rev-parse HEAD)"
git_branch="$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD || echo detached)"
git_dirty=false
[[ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]] || git_dirty=true
[[ "$git_dirty" == false ]] || echo "Warning: building from a dirty worktree (recorded in the manifest)." >&2

mapfile -d '' -t DOCKER < <(docker_engine_command)
COMPOSE=("${DOCKER[@]}" compose)
"${COMPOSE[@]}" version >/dev/null

build_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
release_id="${git_sha:0:12}-${build_hash}"
backend_image="market-data-dwndr-backend:${release_id}"
frontend_image="market-data-dwndr-frontend:${release_id}"

printf 'Building images for release %s...\n' "$release_id"
APP_VERSION="$release_id" "${COMPOSE[@]}" \
    --project-directory "$ROOT_DIR" -f "$ROOT_DIR/compose.yaml" \
    --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" build --pull
backend_image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$backend_image")"
frontend_image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$frontend_image")"

mkdir -p "$RECENT_DIR"
TEMP_BUNDLE="$(mktemp -d "$RECENT_DIR/.export-${release_id}.XXXXXX")"
mkdir -p "$TEMP_BUNDLE/images"

printf 'Saving images...\n'

COMPRESS=(gzip -n -9)
if command -v pigz >/dev/null 2>&1; then
    COMPRESS=(pigz -n -9)
fi

"${DOCKER[@]}" image save "$backend_image" | "${COMPRESS[@]}" > "$TEMP_BUNDLE/images/backend.tar.gz" &
backend_pid=$!

"${DOCKER[@]}" image save "$frontend_image" | "${COMPRESS[@]}" > "$TEMP_BUNDLE/images/frontend.tar.gz" &
frontend_pid=$!

wait "$backend_pid" "$frontend_pid" || { echo "Failed to save and compress images." >&2; exit 1; }

validate_image_archive_tag "$TEMP_BUNDLE/images/backend.tar.gz" "$backend_image"
validate_image_archive_tag "$TEMP_BUNDLE/images/frontend.tar.gz" "$frontend_image"

cp "$DEPLOY_COMPOSE" "$TEMP_BUNDLE/docker-compose.yml"
cp "$SRC_DIR/.env.example" "$TEMP_BUNDLE/.env.example"
cp "$SRC_DIR/README.md" "$TEMP_BUNDLE/README.md"
cp "$SRC_DIR/deploy.sh" "$TEMP_BUNDLE/deploy.sh"
cp "$SRC_DIR/rollback.sh" "$TEMP_BUNDLE/rollback.sh"
chmod +x "$TEMP_BUNDLE/deploy.sh" "$TEMP_BUNDLE/rollback.sh"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
compose_sha="$(sha256sum "$TEMP_BUNDLE/docker-compose.yml" | cut -d' ' -f1)"
backend_sha="$(sha256sum "$TEMP_BUNDLE/images/backend.tar.gz" | cut -d' ' -f1)"
frontend_sha="$(sha256sum "$TEMP_BUNDLE/images/frontend.tar.gz" | cut -d' ' -f1)"

jq -n --arg version "$release_id" '{version: $version}' > "$TEMP_BUNDLE/version.json"
jq -n \
    --arg release_id "$release_id" --arg created_at "$created_at" \
    --arg git_sha "$git_sha" --arg git_branch "$git_branch" --argjson git_dirty "$git_dirty" \
    --arg build_hash "$build_hash" --arg compose_sha "$compose_sha" \
    --arg backend_tag "$backend_image" --arg backend_sha "$backend_sha" --arg backend_id "$backend_image_id" \
    --arg frontend_tag "$frontend_image" --arg frontend_sha "$frontend_sha" --arg frontend_id "$frontend_image_id" \
    '{schema_version: 2, project: "market_data_dwndr", release_id: $release_id, created_at: $created_at,
      git_sha: $git_sha, git_branch: $git_branch, git_dirty: $git_dirty, build_config_hash: $build_hash,
      compose: {file: "docker-compose.yml", sha256: $compose_sha},
      images: {
        backend: {tag: $backend_tag, archive: "images/backend.tar.gz", sha256: $backend_sha, image_id: $backend_id},
        frontend: {tag: $frontend_tag, archive: "images/frontend.tar.gz", sha256: $frontend_sha, image_id: $frontend_id}
      }}' > "$TEMP_BUNDLE/manifest.json"

cat > "$TEMP_BUNDLE/README.txt" <<EOF
DATA_DOWNLOADER release bundle
Release: $release_id
Created: $created_at   Commit: ${git_sha:0:12}$([[ "$git_dirty" == true ]] && echo ' (dirty)')

First VPS deploy:
  1) rsync/scp this whole folder to the VPS deploy dir.
  2) cp .env.example .env  and fill it once (0600).  It is preserved on updates.
  3) ./deploy.sh
Update:   ship again (the build machine's deploy.sh --ship does this).
Rollback: ./rollback.sh   (restores the previous release from ROLLBACK_IMAGE_PATH)
EOF

verify_bundle_sha256 "$TEMP_BUNDLE"
# Keep only the newest staged bundle.
find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '.export-*' -exec rm -rf {} + 2>/dev/null || true
bundle_dir="$RECENT_DIR/v${new_version}-${stamp}"
mv "$TEMP_BUNDLE" "$bundle_dir"
TEMP_BUNDLE=""
printf '\nExported self-contained bundle: %s\n' "$bundle_dir"
