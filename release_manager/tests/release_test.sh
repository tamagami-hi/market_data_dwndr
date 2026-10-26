#!/usr/bin/env bash
# release_test.sh — the release pipeline's tests. No VPS and no real docker:
# the VPS-native scripts run in a temporary stack directory against
# fixtures/fake_docker.sh, and releases are cut in a throwaway repository with
# a bare origin.
#
# Run: ./release_manager/tests/release_test.sh

set -uo pipefail

RM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FAKE_DOCKER="$RM_DIR/tests/fixtures/fake_docker.sh"
PASS=0; FAIL=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

t_ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
t_bad() { printf '  FAIL %s\n' "$1"; [[ -z "${2:-}" ]] || printf '%s\n' "$2" | sed 's/^/       /'; FAIL=$((FAIL + 1)); }
# expect <name> <0|1> <command...> — the command's exit status is (non)zero.
expect() {
    local name="$1" want="$2" out rc; shift 2
    out="$("$@" 2>&1)"; rc=$?
    if [[ "$want" == 0 && $rc -eq 0 ]] || [[ "$want" == 1 && $rc -ne 0 ]]; then t_ok "$name"; else t_bad "$name" "$out"; fi
    LAST_OUT="$out"
}
contains() { if grep -qF -- "$2" <<<"$LAST_OUT"; then t_ok "$1"; else t_bad "$1" "$LAST_OUT"; fi; }

# shellcheck source=../lib/ui.sh
source "$RM_DIR/lib/ui.sh"
# shellcheck source=../lib/version.sh
source "$RM_DIR/lib/version.sh"
# shellcheck source=../lib/stacks.sh
source "$RM_DIR/lib/stacks.sh"
# shellcheck source=../lib/paths.sh
source "$RM_DIR/lib/paths.sh"
# shellcheck source=../lib/ops_release.sh
source "$RM_DIR/lib/ops_release.sh"
# shellcheck source=../lib/ops_export.sh
source "$RM_DIR/lib/ops_export.sh"

CONTRACT="$RM_DIR/stacks/data_downloader/paths.json"

# ── the path contract ───────────────────────────────────────────────────────
printf '\npath contract\n'
expect "the tracked contract validates" 0 paths_validate data_downloader "$CONTRACT"
mutate() { jq "$1" "$CONTRACT" > "$WORK/contract.json"; paths_validate data_downloader "$WORK/contract.json"; }
expect "another stack's contract is refused" 1 mutate '.stack = "broker_login"'
expect "an image prefix the tags do not use is refused" 1 mutate '.image_prefix = "cbl"'
expect "a relative path is refused" 1 mutate '.backup.rollback_images = "rollback/images"'
expect "rollbacks outside the backup tree are refused" 1 mutate '.backup.rollback_images = "/srv/dev_stack/DATA_DOWNLOADER/rollbacks"'
expect "a quote in a path is refused" 1 mutate '.vps.images_dir = "/srv/dev_stack/DATA'"'"'DOWNLOADER/images"'
expect "an archive with a slash is refused" 1 mutate '.images[0].archive = "../backend.tar.gz"'
expect "a short health settle is refused" 1 mutate '.health.settle_seconds = 5'
expect "zero retention is refused" 1 mutate '.retention.keep_releases = 0'

# ── version labels and releases ─────────────────────────────────────────────
printf '\nversion and release\n'
ORIGIN="$WORK/origin.git"; REPO="$WORK/repo"
git init -q --bare -b main "$ORIGIN"
git init -q -b main "$REPO"
git -C "$REPO" config user.name test; git -C "$REPO" config user.email test@example.invalid
mkdir -p "$REPO/backend/app"
printf '"""pkg."""\n\n__version__ = "0.1.51"\n' > "$REPO/backend/app/__init__.py"
git -C "$REPO" add -A; git -C "$REPO" commit -q -m init; git -C "$REPO" tag -a v0.1.51 -m v0.1.51
git -C "$REPO" remote add origin "$ORIGIN"; git -C "$REPO" push -q -u origin main v0.1.51

expect "__version__ is read" 0 test "$(pkg_version "$REPO")" == 0.1.51
expect "a clean tree on its tag builds stable" 0 test "$(build_label "$REPO")" == "0.1.51 stable"
printf '# x\n' >> "$REPO/backend/app/__init__.py"
expect "a dirty tree builds a dev label above the release" 0 \
    bash -c "[[ '$(build_label "$REPO")' =~ ^0\.1\.52-dev\.1\.g[0-9a-f]{9}\.dirty\ dev$ ]]"
git -C "$REPO" checkout -q -- backend/app/__init__.py

ROOT_DIR="$REPO"; UI_INTERACTIVE=false
git -C "$REPO" checkout -q -b feature
expect "a release off main is refused" 1 op_release patch --yes
contains "  …names main" "cut from main only"
git -C "$REPO" checkout -q main
printf 'x\n' > "$REPO/stray"
expect "a release from a dirty tree is refused" 1 op_release patch --yes
rm "$REPO/stray"
expect "a release without --yes and no terminal is refused" 1 op_release patch

expect "a patch release cuts v0.1.52" 0 op_release patch --yes
expect "  …__version__ is 0.1.52" 0 test "$(pkg_version "$REPO")" == 0.1.52
expect "  …the commit touches only __init__.py" 0 \
    test "$(git -C "$REPO" show --name-only --format= HEAD)" == backend/app/__init__.py
expect "  …HEAD is tagged v0.1.52" 0 test "$(git -C "$REPO" describe --exact-match --tags HEAD)" == v0.1.52
expect "  …origin has main and the tag" 0 \
    test "$(git -C "$ORIGIN" rev-parse main)" == "$(git -C "$REPO" rev-parse HEAD)" -a \
         -n "$(git -C "$ORIGIN" tag --list v0.1.52)"
expect "  …the tree now builds 0.1.52 stable" 0 test "$(build_label "$REPO")" == "0.1.52 stable"

git clone -q "$ORIGIN" "$WORK/other"
git -C "$WORK/other" config user.name test; git -C "$WORK/other" config user.email test@example.invalid
printf 'more\n' > "$WORK/other/notes"; git -C "$WORK/other" add notes
git -C "$WORK/other" commit -q -m other; git -C "$WORK/other" push -q origin main
expect "a release behind origin/main is refused" 1 op_release minor --yes
contains "  …says to pull first" "pull --rebase first"
expect "  …and changed nothing" 0 test "$(pkg_version "$REPO")" == 0.1.52

git -C "$REPO" pull -q --rebase origin main
git -C "$REPO" tag -a v0.1.53 -m v0.1.53 HEAD~1 2>/dev/null
expect "a tag that already exists is refused" 1 op_release patch --yes --no-push
git -C "$REPO" tag -d v0.1.53 >/dev/null
expect "a minor release with --no-push stays local" 0 op_release minor --yes --no-push
expect "  …v0.2.0 is not on origin" 0 test -z "$(git -C "$ORIGIN" tag --list v0.2.0)"
git -C "$REPO" tag -a v9.9.9 -m mismatch HEAD
expect "a tag naming another version than __version__ stops the label" 1 build_label "$REPO"

# ── the env template ────────────────────────────────────────────────────────
printf '\nenv template\n'
ROOT_DIR="$(cd "$RM_DIR/.." && pwd)"
expect "the VPS template declares only variables the stack reads" 0 \
    export_assert_env_keys "$RM_DIR/stacks/data_downloader/.env.example"
cp "$RM_DIR/stacks/data_downloader/.env.example" "$WORK/stale.env"
printf 'ROLLBACK_IMAGE_PATH=/srv/backup/x\n' >> "$WORK/stale.env"
expect "a stale deploy variable is caught" 1 export_assert_env_keys "$WORK/stale.env"
contains "  …and named" "ROLLBACK_IMAGE_PATH"
for key in APP_VERSION RELEASE_IMAGE_PATH ROLLBACK_IMAGE_PATH; do
    expect "the template no longer declares $key" 1 grep -q "^$key=" "$RM_DIR/stacks/data_downloader/.env.example"
done

# ── the VPS-native deploy and rollback ──────────────────────────────────────
printf '\nVPS deploy and rollback\n'
VPS="$WORK/vps"; STACK_DIR="$VPS/srv/DATA_DOWNLOADER"; BACKUP="$VPS/backup/DATA_DOWNLOADER"
export FAKE_STATE="$WORK/docker"

# stage_release <version> — what Ship leaves in the stack dir.
stage_release() {
    local version="$1" key
    mkdir -p "$STACK_DIR/images"
    for key in backend frontend; do
        printf 'FAKEIMAGE market-data-dwndr-%s:%s\n' "$key" "$version" | gzip -n > "$STACK_DIR/images/$key.tar.gz"
    done
    cp "$RM_DIR/stacks/data_downloader/docker-compose.yml" "$STACK_DIR/"
    cp "$RM_DIR/stacks/data_downloader/data_downloader_deploy.sh" "$RM_DIR/stacks/data_downloader/data_downloader_rollback.sh" \
       "$RM_DIR/stacks/_shared/"_dd_*.sh "$STACK_DIR/"
    jq --arg d "$FAKE_DOCKER" --arg s "$STACK_DIR" --arg b "$BACKUP" --arg l "$VPS/lock" '
        .vps |= with_entries(.value |= (if type == "string" then sub("^/srv/dev_stack/DATA_DOWNLOADER"; $s) else . end))
        | .backup |= with_entries(.value |= sub("^/srv/backup/DATA_DOWNLOADER"; $b))
        | .vps.docker = $d | .vps.lock_file = $l | .health.settle_seconds = 30' "$CONTRACT" > "$STACK_DIR/paths.json"
    jq -n --arg v "$version" '{version: $v}' > "$STACK_DIR/manifest.json"
    ( cd "$STACK_DIR" && sha256sum manifest.json paths.json docker-compose.yml images/*.tar.gz > checksums.sha256 )
}
deploy()   { ( cd "$STACK_DIR" && DD_TEST_HHMM="${HHMM:-18:00}" ./data_downloader_deploy.sh --yes "$@" ) </dev/null; }
rollback() { ( cd "$STACK_DIR" && DD_TEST_HHMM="${HHMM:-18:00}" ./data_downloader_rollback.sh --yes "$@" ) </dev/null; }
live()     { jq -r .version "$STACK_DIR/version.json"; }
running()  { cat "$FAKE_STATE/containers/market-data-dwndr-backend"; }

# The box as the old pipeline left it: v0.1.52 running, an old-format archive.
mkdir -p "$STACK_DIR/MARKET_DATA/INDICES" "$BACKUP/backup/rollback/images/v0.1.51" "$FAKE_STATE/images" "$FAKE_STATE/containers"
printf 'tick data\n' > "$STACK_DIR/MARKET_DATA/INDICES/keep.bin"
printf 'SECRET=1\n' > "$STACK_DIR/.env"; chmod 600 "$STACK_DIR/.env"
printf '{\n  "version": "v0.1.52"\n}\n' > "$STACK_DIR/version.json"
for key in backend frontend; do
    : > "$FAKE_STATE/images/market-data-dwndr-$key:v0.1.52"
    printf 'FAKEIMAGE market-data-dwndr-%s:v0.1.51\n' "$key" | gzip -n > "$BACKUP/backup/rollback/images/v0.1.51/$key.tar.gz"
done
printf 'v0.1.51\n' > "$BACKUP/backup/rollback/images/v0.1.51/version.txt"
touch -d '2026-08-31 18:00' "$BACKUP/backup/rollback/images/v0.1.51"
printf 'v0.1.52\n' > "$FAKE_STATE/containers/market-data-dwndr-backend"
printf 'v0.1.52\n' > "$FAKE_STATE/containers/market-data-dwndr-frontend"
env_sum="$(sha256sum "$STACK_DIR/.env")"

stage_release 0.1.53
HHMM=10:15 expect "a deploy inside the capture window is refused" 1 deploy
contains "  …and says why" "capture window (09:00-15:30 Asia/Kolkata)"
FAKE_CAPTURE=running FAKE_MARKET_OPEN=11:00 FAKE_MARKET_CLOSE=12:00 HHMM=10:15 \
    expect "the window is the running backend's own" 1 deploy
contains "  …so 10:15 is outside 11:00-12:00, and a running capture still refuses" "capture is running"
FAKE_CAPTURE=down expect "a backend that cannot report its capture refuses" 1 deploy
FAKE_NO_LEASE=1 expect "no maintenance lease refuses before the swap" 1 deploy
expect "  …the old release still runs" 0 test "$(running)" == v0.1.52

FAKE_BAD_CONFIG=0.1.53 expect "a release that rejects the .env is refused before the swap" 1 deploy
contains "  …naming the setting" "KITE_TOKEN_BROKER_TOTP_SEED missing"
expect "  …without echoing input values" 1 grep -q SECRET <<<"$LAST_OUT"
expect "  …the old release still runs" 0 test "$(running)" == v0.1.52
expect "  …and no lease was taken" 0 test ! -s "$FAKE_STATE/leases.log"

expect "a deploy outside the window succeeds" 0 deploy
expect "  …0.1.53 runs" 0 test "$(running)" == 0.1.53
expect "  …version.json records it, and what ran before" 0 \
    test "$(live):$(jq -r .previous "$STACK_DIR/version.json"):$(jq -r .status "$STACK_DIR/version.json")" == "0.1.53:v0.1.52:active"
expect "  …the outgoing v0.1.52, never archived before, is archived now" 0 \
    test -f "$BACKUP/backup/rollback/images/v0.1.52/backend.tar.gz" -a -f "$BACKUP/backup/rollback/images/v0.1.52/checksums.sha256"
expect "  …the lease was taken and released" 0 test "$(cut -d' ' -f1 "$FAKE_STATE/leases.log" | paste -sd' ')" == "taken released"
expect "  …a deploy log was written" 0 test -n "$(ls "$BACKUP/logs/deploy")"
expect "  ….env is untouched" 0 test "$(sha256sum "$STACK_DIR/.env")" == "$env_sum"
expect "  …MARKET_DATA is untouched" 0 test "$(cat "$STACK_DIR/MARKET_DATA/INDICES/keep.bin")" == "tick data"
expect "the same version again is refused without --force" 1 deploy
expect "  …and accepted with --force" 0 deploy --force

stage_release 0.1.54
FAKE_BAD_VERSION=0.1.54 expect "an unhealthy release fails the deploy" 1 deploy
contains "  …and rolls back by itself" "automatically rolled back to 0.1.53"
expect "  …0.1.53 runs again" 0 test "$(running)" == 0.1.53
expect "  …recorded as rolled-back, the failed attempt kept" 0 \
    test "$(jq -r '.status + " " + .last_attempted' "$STACK_DIR/version.json")" == "rolled-back 0.1.54"

expect "rollback lists the complete archives" 0 rollback --list
contains "  …including the old v0.1.51" "v0.1.51"
mkdir -p "$BACKUP/backup/rollback/images/half"
printf 'x' | gzip > "$BACKUP/backup/rollback/images/half/backend.tar.gz"
expect "  …a half-written archive is not offered" 1 grep -q half <<<"$(rollback --list)"
rm -rf "$BACKUP/backup/rollback/images/half"
HHMM=10:15 expect "a rollback inside the window is refused" 1 rollback --to v0.1.51
expect "a rollback to the old-format v0.1.51 works" 0 rollback --to v0.1.51
contains "  …warning that it has no checksums" "no checksums"
expect "  …v0.1.51 runs" 0 test "$(running)" == v0.1.51
expect "  …0.1.53 was archived on the way out" 0 test -f "$BACKUP/backup/rollback/images/0.1.53/frontend.tar.gz"
expect "a bare rollback picks the newest other archive" 0 rollback
expect "  …which is 0.1.53, the one just left" 0 test "$(running)" == 0.1.53

FAKE_BAD_CONFIG=0.1.53 expect "a reload with a rejected .env changes nothing" 1 \
    bash -c 'cd "$1" && DD_TEST_HHMM=18:00 ./data_downloader_deploy.sh --reload --yes </dev/null' _ "$STACK_DIR"
expect "reload recreates the running release" 0 \
    bash -c 'cd "$1" && DD_TEST_HHMM=18:00 ./data_downloader_deploy.sh --reload --yes </dev/null' _ "$STACK_DIR"

for v in 0.2.0 0.2.1 0.2.2 0.2.3 0.2.4; do
    stage_release "$v"; deploy >/dev/null 2>&1 || t_bad "deploy $v for retention"
done
expect "retention keeps the newest 5 archives" 0 test "$(find "$BACKUP/backup/rollback/images" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 5
expect "  …oldest first: v0.1.51 went" 0 test ! -d "$BACKUP/backup/rollback/images/v0.1.51"
expect "  …and images neither running nor archived are removed" 0 test ! -e "$FAKE_STATE/images/market-data-dwndr-backend:v0.1.51"

# ── shell hygiene ───────────────────────────────────────────────────────────
printf '\nshell\n'
expect "every script parses" 0 bash -n "$RM_DIR"/status.sh "$RM_DIR"/lib/*.sh "$RM_DIR"/stacks/_shared/*.sh "$RM_DIR"/stacks/data_downloader/*.sh
if command -v shellcheck >/dev/null; then
    expect "shellcheck is clean" 0 shellcheck -x -S warning "$RM_DIR"/status.sh "$RM_DIR"/lib/*.sh \
        "$RM_DIR"/stacks/_shared/*.sh "$RM_DIR"/stacks/data_downloader/*.sh "$RM_DIR"/tests/*.sh "$FAKE_DOCKER"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
