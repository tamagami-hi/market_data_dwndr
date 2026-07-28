#!/usr/bin/env bash
# rollback_test.sh — exercises DATA_DOWNLOADER/rollback.sh selection logic.
#
# Focuses on the parts that are safe to test without Docker: the saved-release
# inventory, version-aware ordering, and target resolution. The regression that
# motivated these tests: ordering used plain `sort -r`, so with v0.1.7 and v0.1.31
# both present a bare rollback chose v0.1.7 — many releases backwards — while
# reporting it as the newest previous release.
#
# Run: ./release_manager/tests/rollback_test.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT_DIR/release_manager/DATA_DOWNLOADER/rollback.sh"
PASS=0; FAIL=0

ok()   { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  FAIL %s\n     %s\n' "$1" "${2:-}" ; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- fixture: a DATA_DOWNLOADER folder with a populated rollback store -------- #
setup_fixture() {
    local dir="$WORK/dd"; rm -rf "$dir"; mkdir -p "$dir"
    local store="$WORK/ROLLBACKS"; rm -rf "$store"; mkdir -p "$store"

    cp "$SCRIPT" "$dir/rollback.sh"; chmod +x "$dir/rollback.sh"
    printf 'services: {}\n' > "$dir/docker-compose.yml"
    cat > "$dir/.env" <<EOF
APP_VERSION=v0.1.31
ROLLBACK_IMAGE_PATH=$store
RELEASE_IMAGE_PATH=$WORK/releases
HOST_BIND_ADDRESS=127.0.0.1
HTTP_PORT=59001
PORT=59002
EOF

    # Deliberately spans the single/double digit boundary that broke text sorting.
    for v in v0.1.7 v0.1.11 v0.1.26 v0.1.30 v0.1.31; do
        mkdir -p "$store/$v"
        printf 'x' > "$store/$v/backend.tar.gz"
        printf 'x' > "$store/$v/frontend.tar.gz"
        printf '%s\n' "$v" > "$store/$v/version.txt"
    done
    # An interrupted save: only one tarball. Must never be offered.
    mkdir -p "$store/v0.1.29"; printf 'x' > "$store/v0.1.29/backend.tar.gz"

    printf '%s\n' "$dir"
}

# --- 1. --list shows every complete release, newest first -------------------- #
DIR="$(setup_fixture)"
if out="$("$DIR/rollback.sh" --list 2>&1)"; then
    # Only the table rows; the trailing "Currently deployed:" line repeats the active id.
    order="$(sed '/^Currently deployed:/d' <<<"$out" | grep -oE 'v0\.1\.[0-9]+' | paste -sd' ')"
    if [[ "$order" == "v0.1.31 v0.1.30 v0.1.26 v0.1.11 v0.1.7" ]]; then
        ok "--list orders releases version-aware (not lexicographic)"
    else
        bad "--list ordering" "got: $order"
    fi
    grep -q 'currently deployed' <<<"$out" \
        && ok "--list marks the active release" \
        || bad "--list marks the active release" "$out"
    grep -q 'v0.1.29' <<<"$out" \
        && bad "incomplete save must be hidden" "v0.1.29 was listed" \
        || ok "incomplete save (missing frontend tarball) is hidden"
else
    bad "--list runs" "$out"
fi

# --- 2. the ordering regression, stated directly ----------------------------- #
lexicographic="$(printf 'v0.1.30\nv0.1.7\n' | sort -r | head -1)"
versionaware="$(printf 'v0.1.30\nv0.1.7\n' | sort -V -r | head -1)"
[[ "$lexicographic" == "v0.1.7" && "$versionaware" == "v0.1.30" ]] \
    && ok "regression premise holds: sort -r picks v0.1.7, sort -V picks v0.1.30" \
    || bad "regression premise" "lex=$lexicographic ver=$versionaware"

# --- 3. non-TTY resolves to the newest release below current ------------------ #
# Docker is absent/unusable in CI, so the run fails later — we assert only which
# target was chosen, which is decided before any Docker call.
DIR="$(setup_fixture)"
out="$("$DIR/rollback.sh" </dev/null 2>&1 || true)"
if grep -q 'no TTY; falling back to the newest saved release: v0.1.30' <<<"$out"; then
    ok "no TTY falls back to newest-below-current (v0.1.30, not v0.1.7)"
else
    bad "non-TTY fallback target" "$(head -3 <<<"$out")"
fi

# --- 4. --latest agrees with the fallback ------------------------------------ #
DIR="$(setup_fixture)"
out="$("$DIR/rollback.sh" --latest </dev/null 2>&1 || true)"
grep -q 'newest saved release below v0.1.31 is v0.1.30' <<<"$out" \
    && ok "--latest selects v0.1.30" \
    || bad "--latest selection" "$(head -3 <<<"$out")"

# --- 5. refuses the active release and unknown ids --------------------------- #
DIR="$(setup_fixture)"
out="$("$DIR/rollback.sh" -y v0.1.31 </dev/null 2>&1 || true)"
grep -q 'already active' <<<"$out" \
    && ok "refuses to roll back to the active release" \
    || bad "active-release guard" "$(head -3 <<<"$out")"

out="$("$DIR/rollback.sh" -y v9.9.9 </dev/null 2>&1 || true)"
grep -q 'not in the rollback store' <<<"$out" \
    && ok "rejects an unknown release id" \
    || bad "unknown-id guard" "$(head -3 <<<"$out")"

out="$("$DIR/rollback.sh" -y v0.1.29 </dev/null 2>&1 || true)"
grep -q 'incomplete' <<<"$out" \
    && ok "rejects a release with incomplete saved images" \
    || bad "incomplete-save guard" "$(head -3 <<<"$out")"

# --- 6. help works without an env file --------------------------------------- #
out="$("$SCRIPT" --help 2>&1 || true)"
grep -q 'interactive picker' <<<"$out" \
    && ok "--help documents the interactive picker" \
    || bad "--help output" "$(head -3 <<<"$out")"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
