#!/usr/bin/env bash

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC_RM="$SOURCE_ROOT/release_manager"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
REPO="$TEST_ROOT/repo"
FAKE_BIN="$TEST_ROOT/bin"

mkdir -p "$REPO/release_manager/lib" "$REPO/release_manager/DATA_DOWNLOADER/images" \
    "$REPO/backend/app" "$REPO/frontend" "$FAKE_BIN"
cp "$SRC_RM/export.sh" "$REPO/release_manager/export.sh"
cp "$SRC_RM/lib/common.sh" "$REPO/release_manager/lib/common.sh"
cp "$SRC_RM/compose.deploy.yaml" "$REPO/release_manager/compose.deploy.yaml"
cp "$SRC_RM/DATA_DOWNLOADER/.env.example" "$REPO/release_manager/DATA_DOWNLOADER/.env.example"
cp "$SRC_RM/DATA_DOWNLOADER/deploy.sh" "$REPO/release_manager/DATA_DOWNLOADER/deploy.sh"
cp "$SRC_RM/DATA_DOWNLOADER/rollback.sh" "$REPO/release_manager/DATA_DOWNLOADER/rollback.sh"
cp "$SRC_RM/DATA_DOWNLOADER/README.md" "$REPO/release_manager/DATA_DOWNLOADER/README.md"
chmod +x "$REPO/release_manager/export.sh"

printf 'APP_UID=10001\nAPP_GID=10001\nHTTP_PORT=9000\n' > "$REPO/backend/.env"
printf '__version__ = "0.1.0"\n' > "$REPO/backend/app/__init__.py"
printf 'PORT=3789\nNEXT_PUBLIC_BACKEND_URL=http://localhost:9000\nNEXT_PUBLIC_APP_NAME=TickVault\n' \
    > "$REPO/frontend/.env.local"
printf 'services: {}\n' > "$REPO/compose.yaml"
printf 'release_manager/.env\n' > "$REPO/.gitignore"

cp "$SRC_RM/tests/fixtures/fake_docker.sh" "$FAKE_BIN/docker"
chmod +x "$FAKE_BIN/docker"

git -C "$REPO" init --quiet -b main
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
git -C "$REPO" add .
git -C "$REPO" commit --quiet -m fixture

PATH="$FAKE_BIN:$PATH" TMPDIR="$TEST_ROOT" "$REPO/release_manager/export.sh" >/dev/null

mapfile -t bundles < <(find "$REPO/release_manager/recent_builds" -mindepth 1 -maxdepth 1 -type d -print)
[[ ${#bundles[@]} -eq 1 ]] || { echo "expected exactly one bundle" >&2; exit 1; }
BUNDLE="${bundles[0]}"

# shellcheck source=../lib/common.sh
source "$REPO/release_manager/lib/common.sh"
verify_bundle_sha256 "$BUNDLE"
for f in docker-compose.yml .env.example deploy.sh rollback.sh manifest.json version.json README.txt README.md \
    images/backend.tar.gz images/frontend.tar.gz; do
    [[ -e "$BUNDLE/$f" ]] || { echo "bundle missing $f" >&2; exit 1; }
done
[[ -x "$BUNDLE/deploy.sh" && -x "$BUNDLE/rollback.sh" ]] || { echo "runners must be executable" >&2; exit 1; }
[[ "$(jq -r '.git_sha' "$BUNDLE/manifest.json")" == "$(git -C "$REPO" rev-parse HEAD)" ]]
# A full export must declare both services.
[[ "$(jq -rc '.services' "$BUNDLE/manifest.json")" == '["backend","frontend"]' ]] \
    || { echo "full bundle must list both services" >&2; exit 1; }
# secrets must never be bundled
if find "$BUNDLE" -type f -name '.env' -print -quit | grep -q .; then
    echo "bundle must not contain a real .env" >&2; exit 1; fi

# --- PARTIAL export: --frontend must bundle ONLY the frontend image ----------
PATH="$FAKE_BIN:$PATH" TMPDIR="$TEST_ROOT" "$REPO/release_manager/export.sh" --frontend >/dev/null
mapfile -t bundles2 < <(find "$REPO/release_manager/recent_builds" -mindepth 1 -maxdepth 1 -type d -print)
[[ ${#bundles2[@]} -eq 1 ]] || { echo "expected exactly one staged bundle after partial export" >&2; exit 1; }
PB="${bundles2[0]}"

[[ "$(jq -rc '.services' "$PB/manifest.json")" == '["frontend"]' ]] \
    || { echo "partial bundle must list only frontend, got $(jq -rc '.services' "$PB/manifest.json")" >&2; exit 1; }
[[ -f "$PB/images/frontend.tar.gz" ]] || { echo "partial bundle missing frontend image" >&2; exit 1; }
[[ ! -f "$PB/images/backend.tar.gz" ]] \
    || { echo "partial frontend bundle must NOT contain a backend image" >&2; exit 1; }
[[ "$(jq -r '.images.backend // "absent"' "$PB/manifest.json")" == "absent" ]] \
    || { echo "partial manifest must not describe a backend image" >&2; exit 1; }
# The integrity check must accept a partial bundle (it only verifies what is shipped).
verify_bundle_sha256 "$PB" || { echo "partial bundle must pass checksum verification" >&2; exit 1; }
mapfile -t pservices < <(bundle_services "$PB")
[[ "${pservices[*]}" == "frontend" ]] || { echo "bundle_services must report frontend only" >&2; exit 1; }
grep -q 'Scope:' "$PB/README.txt" || { echo "bundle README should record the scope" >&2; exit 1; }

printf 'release export integration test passed\n'
