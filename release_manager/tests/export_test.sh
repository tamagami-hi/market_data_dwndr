#!/usr/bin/env bash

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
REPO="$TEST_ROOT/repo"
REMOTE="$TEST_ROOT/origin.git"
FAKE_BIN="$TEST_ROOT/bin"

mkdir -p "$REPO/release_manager/lib" "$REPO/backend" "$REPO/frontend" "$FAKE_BIN"
cp "$SOURCE_ROOT/release_manager/export.sh" "$REPO/release_manager/export.sh"
cp "$SOURCE_ROOT/release_manager/lib/common.sh" "$REPO/release_manager/lib/common.sh"
chmod +x "$REPO/release_manager/export.sh"

printf '%s\n' \
    'APP_UID=10001' \
    'APP_GID=10001' \
    'HTTP_PORT=9000' \
    > "$REPO/backend/.env"
printf '%s\n' \
    'PORT=3001' \
    'NEXT_PUBLIC_BACKEND_URL=http://100.64.0.1:9000' \
    > "$REPO/frontend/.env.local"
printf 'services: {}\n' > "$REPO/compose.yaml"
printf 'release_manager/.env\nrelease_manager/.operation.lock\n' > "$REPO/.gitignore"

cp "$SOURCE_ROOT/release_manager/tests/fixtures/fake_docker.sh" "$FAKE_BIN/docker"
chmod +x "$FAKE_BIN/docker"

git init --bare --quiet "$REMOTE"
git -C "$REPO" init --quiet -b main
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
git -C "$REPO" add .
git -C "$REPO" commit --quiet -m fixture
git -C "$REPO" remote add origin "$REMOTE"
git -C "$REPO" push --quiet -u origin main
openssl genpkey -algorithm ED25519 -out "$TEST_ROOT/release-private.pem" 2>/dev/null
openssl pkey -in "$TEST_ROOT/release-private.pem" -pubout \
    -out "$TEST_ROOT/release-public.pem" 2>/dev/null
printf 'RELEASE_SIGNING_PRIVATE_KEY=%s\nRELEASE_SIGNING_PUBLIC_KEY=%s\n' \
    "$TEST_ROOT/release-private.pem" "$TEST_ROOT/release-public.pem" \
    > "$REPO/release_manager/.env"

PATH="$FAKE_BIN:$PATH" "$REPO/release_manager/export.sh" >/dev/null
mapfile -t bundles < <(find "$REPO/release_manager/recent_builds" -mindepth 1 -maxdepth 1 \
    -type d -print)
[[ ${#bundles[@]} -eq 1 ]]

# shellcheck source=../lib/common.sh
source "$REPO/release_manager/lib/common.sh"
validate_release_bundle "${bundles[0]}"
validate_signed_release_bundle "${bundles[0]}" \
    "$TEST_ROOT/release-public.pem"
[[ "$(jq -r '.git_sha' "${bundles[0]}/manifest.json")" == "$(git -C "$REPO" rev-parse HEAD)" ]]
if find "${bundles[0]}" -type f \( -name '.env' -o -name '.env.local' -o -name '.env.*' \) \
    -print -quit | grep -q .; then
    echo "Exported bundle contains an environment file." >&2
    exit 1
fi

printf 'release export integration test passed\n'
