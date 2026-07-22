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
