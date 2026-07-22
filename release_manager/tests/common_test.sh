#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT_DIR/release_manager/lib/common.sh"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
BACKEND_ENV="$TEST_DIR/backend.env"
FRONTEND_ENV="$TEST_DIR/frontend.env"

printf 'HOST_BIND_ADDRESS=127.0.0.1\nHTTP_PORT=9000\nAPP_UID=10001\nAPP_GID=10001\n' > "$BACKEND_ENV"
printf 'NEXT_PUBLIC_BACKEND_URL=http://localhost:9000\n' > "$FRONTEND_ENV"

first_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
second_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
[[ "$first_hash" == "$second_hash" ]]
[[ "$first_hash" =~ ^[[:xdigit:]]{12}$ ]]

printf 'NEXT_PUBLIC_BACKEND_URL=http://localhost:9100\n' > "$FRONTEND_ENV"
changed_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
[[ "$changed_hash" != "$first_hash" ]]

printf 'HOST_BIND_ADDRESS=127.0.0.1\nHTTP_PORT=9000\nAPP_UID=10002\nAPP_GID=10001\n' > "$BACKEND_ENV"
changed_uid_hash="$(image_build_config_hash "$BACKEND_ENV" "$FRONTEND_ENV")"
[[ "$changed_uid_hash" != "$changed_hash" ]]

if (
    curl() { return 1; }
    assert_capture_stopped "$BACKEND_ENV" true
); then
    echo "Expected an unreachable existing deployment to block release." >&2
    exit 1
fi

(
    curl() { return 1; }
    assert_capture_stopped "$BACKEND_ENV" false
)

if (
    curl() { printf '{"running":true}'; }
    assert_capture_stopped "$BACKEND_ENV" true
); then
    echo "Expected a running capture to block release." >&2
    exit 1
fi

(
    curl() { printf '{"running":false}'; }
    assert_capture_stopped "$BACKEND_ENV" true
)

if (
    curl() { printf '{"unexpected":"response"}'; }
    assert_capture_stopped "$BACKEND_ENV" true
); then
    echo "Expected a malformed capture response to block release." >&2
    exit 1
fi

printf 'release manager common tests passed\n'

# Release bundles are immutable, checksummed, and must never contain deployment
# secrets. Build a minimal valid fixture, then prove each integrity guard fails
# closed when its input is changed.
BUNDLE_DIR="$TEST_DIR/bundle"
mkdir -p "$BUNDLE_DIR/images"
printf 'backend image\n' > "$BUNDLE_DIR/images/backend.tar.gz"
printf 'frontend image\n' > "$BUNDLE_DIR/images/frontend.tar.gz"
printf 'services: {}\n' > "$BUNDLE_DIR/docker-compose.yml"

release_id="0123456789ab-abcdef012345"
backend_sha="$(sha256sum "$BUNDLE_DIR/images/backend.tar.gz" | cut -d' ' -f1)"
frontend_sha="$(sha256sum "$BUNDLE_DIR/images/frontend.tar.gz" | cut -d' ' -f1)"
compose_sha="$(sha256sum "$BUNDLE_DIR/docker-compose.yml" | cut -d' ' -f1)"
printf '{"version":"%s"}\n' "$release_id" > "$BUNDLE_DIR/version.json"
jq -n \
    --arg release_id "$release_id" \
    --arg backend_sha "$backend_sha" \
    --arg frontend_sha "$frontend_sha" \
    --arg compose_sha "$compose_sha" \
    '{schema_version: 1, release_id: $release_id, git_sha: "0123456789abcdef0123456789abcdef01234567", git_dirty: false,
      compose: {file: "docker-compose.yml", sha256: $compose_sha},
      images: {
        backend: {tag: ("market-data-dwndr-backend:" + $release_id), archive: "images/backend.tar.gz", sha256: $backend_sha, image_id: ("sha256:" + ("a" * 64))},
        frontend: {tag: ("market-data-dwndr-frontend:" + $release_id), archive: "images/frontend.tar.gz", sha256: $frontend_sha, image_id: ("sha256:" + ("b" * 64))}
      }}' > "$BUNDLE_DIR/manifest.json"

validate_release_bundle "$BUNDLE_DIR"
[[ "$(release_bundle_version "$BUNDLE_DIR")" == "$release_id" ]]

private_key="$TEST_DIR/release-private.pem"
public_key="$TEST_DIR/release-public.pem"
openssl genpkey -algorithm ED25519 -out "$private_key" 2>/dev/null
openssl pkey -in "$private_key" -pubout -out "$public_key" 2>/dev/null
sign_release_manifest "$BUNDLE_DIR" "$private_key"
validate_signed_release_bundle "$BUNDLE_DIR" "$public_key"
printf 'invalid-signature\n' > "$BUNDLE_DIR/manifest.sig"
if validate_signed_release_bundle "$BUNDLE_DIR" "$public_key" >/dev/null 2>&1; then
    echo "Expected a modified manifest signature to fail validation." >&2
    exit 1
fi
sign_release_manifest "$BUNDLE_DIR" "$private_key"

printf 'tampered\n' >> "$BUNDLE_DIR/images/backend.tar.gz"
if validate_release_bundle "$BUNDLE_DIR" >/dev/null 2>&1; then
    echo "Expected a modified image archive to fail bundle validation." >&2
    exit 1
fi
printf 'backend image\n' > "$BUNDLE_DIR/images/backend.tar.gz"
sign_release_manifest "$BUNDLE_DIR" "$private_key"

printf 'KITE_PASSWORD=must-not-ship\n' > "$BUNDLE_DIR/.env"
if validate_release_bundle "$BUNDLE_DIR" >/dev/null 2>&1; then
    echo "Expected a bundled .env to fail bundle validation." >&2
    exit 1
fi
rm "$BUNDLE_DIR/.env"

printf 'unexpected\n' > "$BUNDLE_DIR/secret.txt"
if validate_release_bundle "$BUNDLE_DIR" >/dev/null 2>&1; then
    echo "Expected an unexpected bundle file to fail validation." >&2
    exit 1
fi
rm "$BUNDLE_DIR/secret.txt"

OLD_ACTIVE="$TEST_DIR/old-active"
copy_release_bundle "$BUNDLE_DIR" "$OLD_ACTIVE"
cp_calls=0
cp() {
    cp_calls=$((cp_calls + 1))
    if [[ "$cp_calls" -eq 3 ]]; then
        return 1
    fi
    command cp "$@"
}
if copy_release_bundle "$BUNDLE_DIR" "$OLD_ACTIVE" >/dev/null 2>&1; then
    echo "Expected injected staging copy failure." >&2
    exit 1
fi
unset -f cp
validate_signed_release_bundle "$OLD_ACTIVE" "$public_key"

STAGED_ACTIVE="$TEST_DIR/staged-active"
mkdir -p "$STAGED_ACTIVE/images"
truncate -s 0 "$STAGED_ACTIVE/.gitkeep" "$STAGED_ACTIVE/images/.gitkeep"
prepared_bundle="$(prepare_release_bundle "$BUNDLE_DIR" "$STAGED_ACTIVE")"
[[ ! -f "$STAGED_ACTIVE/manifest.json" ]]
retired_bundle="$(activate_prepared_bundle "$prepared_bundle" "$STAGED_ACTIVE")"
validate_signed_release_bundle "$STAGED_ACTIVE" "$public_key"
[[ -d "$retired_bundle" && ! -f "$retired_bundle/manifest.json" ]]
rm -rf -- "$retired_bundle"

TTL_ENV="$TEST_DIR/backend.env"
printf 'RELEASE_MAINTENANCE_TTL_SECONDS=900\n' > "$TTL_ENV"
validate_release_maintenance_ttl "$TTL_ENV"
printf 'RELEASE_MAINTENANCE_TTL_SECONDS=300\n' > "$TTL_ENV"
if validate_release_maintenance_ttl "$TTL_ENV" >/dev/null 2>&1; then
    echo "Expected a short release-maintenance TTL to fail validation." >&2
    exit 1
fi
future_expiry="$(date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)"
lease_response="$(jq -cn --arg expiry "$future_expiry" \
    '{lease_id: "1234567890abcdef", expires_at: $expiry}')"
[[ "$(maintenance_lease_id "$lease_response")" == "1234567890abcdef" ]]
validate_maintenance_lease_remaining "$lease_response" 300
expired_response="$(jq -cn \
    '{lease_id: "1234567890abcdef", expires_at: "2020-01-01T00:00:00Z"}')"
if validate_maintenance_lease_remaining "$expired_response" 300 >/dev/null 2>&1; then
    echo "Expected a nearly expired maintenance lease to fail validation." >&2
    exit 1
fi

ARCHIVE_FIXTURE="$TEST_DIR/archive-fixture"
mkdir -p "$ARCHIVE_FIXTURE"
printf '[{"Config":"config.json","RepoTags":["market-data-dwndr-backend:%s"],"Layers":[]}]\n' \
    "$release_id" > "$ARCHIVE_FIXTURE/manifest.json"
tar -czf "$TEST_DIR/exact-tag.tar.gz" -C "$ARCHIVE_FIXTURE" manifest.json
validate_image_archive_tag "$TEST_DIR/exact-tag.tar.gz" \
    "market-data-dwndr-backend:$release_id"
if validate_image_archive_tag "$TEST_DIR/exact-tag.tar.gz" \
    "market-data-dwndr-frontend:$release_id" >/dev/null 2>&1; then
    echo "Expected an archive with the wrong tag to fail validation." >&2
    exit 1
fi

RELEASE_TEST_HHMM=10:00
if assert_outside_capture_window "09:00" "15:30" "Asia/Kolkata" >/dev/null 2>&1; then
    echo "Expected deployment during capture hours to be refused." >&2
    exit 1
fi
RELEASE_TEST_HHMM=16:00 assert_outside_capture_window "09:00" "15:30" "Asia/Kolkata"
unset RELEASE_TEST_HHMM

LOCK_FILE="$TEST_DIR/release-operation.lock"
exec {HOLDER_FD}>"$LOCK_FILE"
flock -n "$HOLDER_FD"
if acquire_release_lock "$LOCK_FILE" >/dev/null 2>&1; then
    echo "Expected a concurrent release operation to be refused." >&2
    exit 1
fi
flock -u "$HOLDER_FD"
acquire_release_lock "$LOCK_FILE"

validate_tailscale_ipv4 "100.64.0.1"
validate_tailscale_ipv4 "100.127.255.254"
for invalid_host in "100.63.1.1" "100.128.1.1" "8.8.8.8" "example.com" "100.64.999.1"; do
    if validate_tailscale_ipv4 "$invalid_host"; then
        echo "Expected non-Tailscale host to be rejected: $invalid_host" >&2
        exit 1
    fi
done

printf '{"version":"different"}\n' > "$BUNDLE_DIR/version.json"
if validate_release_bundle "$BUNDLE_DIR" >/dev/null 2>&1; then
    echo "Expected mismatched version metadata to fail bundle validation." >&2
    exit 1
fi

printf 'release bundle integrity tests passed\n'
