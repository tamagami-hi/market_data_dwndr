#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT_DIR/release_manager/lib/common.sh"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
BACKEND_ENV="$TEST_DIR/backend.env"
FRONTEND_ENV="$TEST_DIR/frontend.env"

# --- env_value / set_env_value ---------------------------------------------
printf 'A=1\nB=two\n' > "$BACKEND_ENV"
[[ "$(env_value "$BACKEND_ENV" B)" == "two" ]]
set_env_value "$BACKEND_ENV" B changed
[[ "$(env_value "$BACKEND_ENV" B)" == "changed" ]]
set_env_value "$BACKEND_ENV" C added
[[ "$(env_value "$BACKEND_ENV" C)" == "added" ]]

# --- image_build_config_hash (deterministic; sensitive to inputs) -----------
printf 'HTTP_PORT=9000\nAPP_UID=10001\nAPP_GID=10001\n' > "$BACKEND_ENV"
printf 'NEXT_PUBLIC_BACKEND_URL=http://localhost:9000\nNEXT_PUBLIC_APP_NAME=TickVault\n' > "$FRONTEND_ENV"
h1="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
[[ "$h1" =~ ^[[:xdigit:]]{12}$ ]]
[[ "$h1" == "$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")" ]]
printf 'NEXT_PUBLIC_BACKEND_URL=http://localhost:9000\nNEXT_PUBLIC_APP_NAME=Other\n' > "$FRONTEND_ENV"
[[ "$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")" != "$h1" ]]

# --- assert_capture_stopped -------------------------------------------------
printf 'HOST_BIND_ADDRESS=127.0.0.1\nHTTP_PORT=9000\n' > "$BACKEND_ENV"
if ( curl() { return 1; }; assert_capture_stopped "$BACKEND_ENV" true ); then
    echo "unreachable stack must block" >&2; exit 1; fi
( curl() { return 1; }; assert_capture_stopped "$BACKEND_ENV" false )
if ( curl() { printf '{"running":true}'; }; assert_capture_stopped "$BACKEND_ENV" true ); then
    echo "running capture must block" >&2; exit 1; fi
( curl() { printf '{"running":false}'; }; assert_capture_stopped "$BACKEND_ENV" true )

# --- assert_outside_capture_window ------------------------------------------
if RELEASE_TEST_HHMM=10:00 assert_outside_capture_window "09:00" "15:30" "Asia/Kolkata" >/dev/null 2>&1; then
    echo "deploy during capture hours must be refused" >&2; exit 1; fi
RELEASE_TEST_HHMM=16:00 assert_outside_capture_window "09:00" "15:30" "Asia/Kolkata"

# --- validate_image_archive_tag + verify_bundle_sha256 ----------------------
BUNDLE="$TEST_DIR/bundle"; mkdir -p "$BUNDLE/images"
fake_image() { local dir tag=$1 out=$2; dir="$(mktemp -d)"
    printf '[{"Config":"c","RepoTags":["%s"],"Layers":[]}]\n' "$tag" > "$dir/manifest.json"
    tar -cf - -C "$dir" manifest.json | gzip -n > "$out"; rm -rf "$dir"; }
rid="0123456789ab-abcdef012345"
fake_image "market-data-dwndr-backend:$rid" "$BUNDLE/images/backend.tar.gz"
fake_image "market-data-dwndr-frontend:$rid" "$BUNDLE/images/frontend.tar.gz"
printf 'services: {}\n' > "$BUNDLE/docker-compose.yml"
validate_image_archive_tag "$BUNDLE/images/backend.tar.gz" "market-data-dwndr-backend:$rid"
if validate_image_archive_tag "$BUNDLE/images/backend.tar.gz" "wrong:tag" >/dev/null 2>&1; then
    echo "wrong tag must fail" >&2; exit 1; fi
bsha="$(sha256sum "$BUNDLE/images/backend.tar.gz" | cut -d' ' -f1)"
fsha="$(sha256sum "$BUNDLE/images/frontend.tar.gz" | cut -d' ' -f1)"
csha="$(sha256sum "$BUNDLE/docker-compose.yml" | cut -d' ' -f1)"
printf '{"version":"%s"}\n' "$rid" > "$BUNDLE/version.json"
jq -n --arg c "$csha" --arg b "$bsha" --arg f "$fsha" \
    '{compose:{sha256:$c}, images:{backend:{sha256:$b}, frontend:{sha256:$f}}}' > "$BUNDLE/manifest.json"
verify_bundle_sha256 "$BUNDLE"
[[ "$(release_bundle_version "$BUNDLE")" == "$rid" ]]
printf 'tamper\n' >> "$BUNDLE/images/backend.tar.gz"
if verify_bundle_sha256 "$BUNDLE" >/dev/null 2>&1; then
    echo "tampered archive must fail checksum" >&2; exit 1; fi

# --- acquire_release_lock (concurrent refused) ------------------------------
LOCK="$TEST_DIR/op.lock"
exec {FD}>"$LOCK"; flock -n "$FD"
if acquire_release_lock "$LOCK" >/dev/null 2>&1; then
    echo "concurrent release must be refused" >&2; exit 1; fi
flock -u "$FD"
acquire_release_lock "$LOCK"

printf 'release manager common tests passed\n'
