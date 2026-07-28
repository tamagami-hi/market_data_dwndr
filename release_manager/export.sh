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

usage() {
    cat <<'EOF'
Usage: ./release_manager/export.sh [--major|--minor|--patch] [--backend-url URL]
                                   [--frontend] [--backend]

  --frontend          Build and bundle ONLY the frontend image.
  --backend           Build and bundle ONLY the backend image.
                      Omit both to build the whole stack (the default).
                      A partial bundle records its contents in manifest.json, and the
                      deploy skips (and re-tags) the service it does not ship, so a
                      frontend-only release never restarts the backend.

  --backend-url URL   Bake an ABSOLUTE backend origin into the frontend image (the
                      browser calls that origin directly). Omit for the default
                      SAME-ORIGIN build (empty NEXT_PUBLIC_BACKEND_URL: relative /api +
                      window.location WS, fronted by a reverse proxy). Same-origin
                      images are domain/scheme-agnostic — no rebuild to rename/retls.
EOF
}
BUMP="patch"
# Same-origin is the default shipped build (empty NEXT_PUBLIC_BACKEND_URL). This is
# decoupled from frontend/.env.local, which stays absolute for local `next dev`.
build_backend_url=""
want_frontend=false
want_backend=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --major) BUMP="major"; shift ;;
        --minor) BUMP="minor"; shift ;;
        --patch) BUMP="patch"; shift ;;
        --frontend) want_frontend=true; shift ;;
        --backend) want_backend=true; shift ;;
        --backend-url)
            [[ $# -ge 2 ]] || { echo "--backend-url requires a value" >&2; usage >&2; exit 1; }
            build_backend_url="$2"; shift 2 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done
# Neither flag (or both) means the full stack.
if [[ "$want_frontend" == false && "$want_backend" == false ]]; then
    want_frontend=true; want_backend=true
fi
SERVICES=()
[[ "$want_backend" == true ]] && SERVICES+=(backend)
[[ "$want_frontend" == true ]] && SERVICES+=(frontend)
PARTIAL=false
[[ ${#SERVICES[@]} -eq 2 ]] || PARTIAL=true

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

build_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV" "$build_backend_url")"
release_id="v${new_version}"
backend_image="market-data-dwndr-backend:${release_id}"
frontend_image="market-data-dwndr-frontend:${release_id}"

if [[ "$PARTIAL" == true ]]; then
    printf 'Bundle scope: PARTIAL — %s only (the other service is left untouched on deploy)\n' \
        "${SERVICES[*]}"
else
    printf 'Bundle scope: full stack (backend + frontend)\n'
fi
if [[ "$want_frontend" == true ]]; then
    if [[ -z "$build_backend_url" ]]; then
        printf 'Frontend build mode: SAME-ORIGIN (relative /api + window.location WS; reverse-proxy fronted)\n'
    else
        printf 'Frontend build mode: ABSOLUTE backend URL = %s\n' "$build_backend_url"
    fi
fi

printf 'Building images for release %s...\n' "$release_id"
# Force the effective NEXT_PUBLIC_BACKEND_URL for the build: a shell env var takes
# precedence over the --env-file value, so this overrides the dev-oriented .env.local
# (empty => same-origin). NEXT_PUBLIC_APP_NAME still comes from the env file.
NEXT_PUBLIC_BACKEND_URL="$build_backend_url" APP_VERSION="$release_id" "${COMPOSE[@]}" \
    --project-directory "$ROOT_DIR" -f "$ROOT_DIR/compose.yaml" \
    --env-file "$BACKEND_ENV" --env-file "$FRONTEND_ENV" build --pull "${SERVICES[@]}"

declare -A IMAGE_TAG=([backend]="$backend_image" [frontend]="$frontend_image")
declare -A IMAGE_ID=()
for service in "${SERVICES[@]}"; do
    IMAGE_ID[$service]="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "${IMAGE_TAG[$service]}")"
done

mkdir -p "$RECENT_DIR"
TEMP_BUNDLE="$(mktemp -d "$RECENT_DIR/.export-${release_id}.XXXXXX")"
mkdir -p "$TEMP_BUNDLE/images"

printf 'Saving images...\n'

COMPRESS=(gzip -n -9)
if command -v pigz >/dev/null 2>&1; then
    COMPRESS=(pigz -n -9)
fi

save_pids=()
for service in "${SERVICES[@]}"; do
    "${DOCKER[@]}" image save "${IMAGE_TAG[$service]}" \
        | "${COMPRESS[@]}" > "$TEMP_BUNDLE/images/${service}.tar.gz" &
    save_pids+=("$!")
done
wait "${save_pids[@]}" || { echo "Failed to save and compress images." >&2; exit 1; }

for service in "${SERVICES[@]}"; do
    validate_image_archive_tag "$TEMP_BUNDLE/images/${service}.tar.gz" "${IMAGE_TAG[$service]}"
done

cp "$DEPLOY_COMPOSE" "$TEMP_BUNDLE/docker-compose.yml"
cp "$SRC_DIR/.env.example" "$TEMP_BUNDLE/.env.example"
cp "$SRC_DIR/README.md" "$TEMP_BUNDLE/README.md"
cp "$SRC_DIR/deploy.sh" "$TEMP_BUNDLE/deploy.sh"
cp "$SRC_DIR/rollback.sh" "$TEMP_BUNDLE/rollback.sh"
chmod +x "$TEMP_BUNDLE/deploy.sh" "$TEMP_BUNDLE/rollback.sh"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
compose_sha="$(sha256sum "$TEMP_BUNDLE/docker-compose.yml" | cut -d' ' -f1)"

jq -n --arg version "$release_id" '{version: $version}' > "$TEMP_BUNDLE/version.json"

# Build the images object from the services this bundle actually ships. Consumers read
# `.services` (see bundle_services in lib/common.sh) instead of assuming both exist.
images_json='{}'
for service in "${SERVICES[@]}"; do
    sha="$(sha256sum "$TEMP_BUNDLE/images/${service}.tar.gz" | cut -d' ' -f1)"
    images_json="$(jq -n \
        --argjson acc "$images_json" --arg svc "$service" \
        --arg tag "${IMAGE_TAG[$service]}" --arg sha "$sha" --arg id "${IMAGE_ID[$service]}" \
        '$acc + {($svc): {tag: $tag, archive: ("images/" + $svc + ".tar.gz"),
                          sha256: $sha, image_id: $id}}')"
done
services_json="$(printf '%s\n' "${SERVICES[@]}" | jq -R . | jq -s .)"

jq -n \
    --arg release_id "$release_id" --arg created_at "$created_at" \
    --arg git_sha "$git_sha" --arg git_branch "$git_branch" --argjson git_dirty "$git_dirty" \
    --arg build_hash "$build_hash" --arg compose_sha "$compose_sha" \
    --argjson services "$services_json" --argjson images "$images_json" \
    '{schema_version: 3, project: "market_data_dwndr", release_id: $release_id,
      created_at: $created_at, git_sha: $git_sha, git_branch: $git_branch,
      git_dirty: $git_dirty, build_config_hash: $build_hash,
      services: $services,
      compose: {file: "docker-compose.yml", sha256: $compose_sha},
      images: $images}' > "$TEMP_BUNDLE/manifest.json"

cat > "$TEMP_BUNDLE/README.txt" <<EOF
DATA_DOWNLOADER release bundle
Release: $release_id
Scope:   ${SERVICES[*]}$([[ "$PARTIAL" == true ]] && echo '  (PARTIAL — the service not listed is left running and only re-tagged)')
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
