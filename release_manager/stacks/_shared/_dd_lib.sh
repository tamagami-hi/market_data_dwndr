#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# _dd_lib.sh — shared VPS-native runtime library.
#
# Runs ON THE VPS. Sourced by the native deploy and rollback scripts. Everything
# it does is driven by paths.json, so the deploy and the rollback cannot drift
# apart. Ported from the broker login stack's _cbl_lib.sh; the differences are
# the downloader's own: two images, Docker's own health checks as the gate, and
# the capture guards (market window, a running capture, the writer drain).
#
# `docker`, never `sudo docker`: the deploy user is in the docker group.
#
# THIS BOX IS SHARED. It also runs the boe-*, cbl-* and aie-* services. Every
# docker command here is scoped to this stack's compose project, its container
# names or its own image tags. Nothing here prunes images, volumes or networks
# globally, and nothing may: `docker system prune` on this host would reach
# another stack's rollback images.
#
# .env belongs entirely to the operator. Nothing here reads, writes or removes
# it. The few values the guards need (the market window, the backend port, the
# maintenance token) are read INSIDE the running backend container, from the
# environment Docker gave it, never from the file.
# ─────────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
    _c_rst=$'\033[0m'; _c_bold=$'\033[1m'; _c_dim=$'\033[2m'
    _c_red=$'\033[31m'; _c_grn=$'\033[32m'; _c_ylw=$'\033[33m'; _c_cyn=$'\033[36m'
else
    _c_rst=''; _c_bold=''; _c_dim=''; _c_red=''; _c_grn=''; _c_ylw=''; _c_cyn=''
fi

declare -A P=()
DD_LOG_FILE=''
DD_LEASE_ID=''

step() { printf '\n%s→%s %s\n' "$_c_cyn" "$_c_rst" "$1"; log "STEP $1"; }
ok()   { printf '   %s✓%s %s\n' "$_c_grn" "$_c_rst" "$1"; log "OK $1"; }
warn() { printf '   %s!%s %s\n' "$_c_ylw" "$_c_rst" "$1" >&2; log "WARN $1"; }
info() { printf '     %s%s%s\n' "$_c_dim" "$1" "$_c_rst"; log "INFO $1"; }
die()  { printf '   %s✗%s %s\n' "$_c_red" "$_c_rst" "$1" >&2; log "FAIL $1"; exit 1; }

log() {
    [[ -n "$DD_LOG_FILE" ]] || return 0
    printf '%s %s\n' "$(date -Is)" "$1" >> "$DD_LOG_FILE" 2>/dev/null || true
}

require_cmds() {
    local c
    for c in "$@"; do command -v "$c" >/dev/null || die "$c is required on this host"; done
}

# ── contract ────────────────────────────────────────────────────────────────
# dd_load_paths <paths.json> — read the contract into P[].
#
# Read once into an associative array so a later step cannot re-read a file that
# changed underneath it mid-deploy.
dd_load_paths() {
    local file="$1" key value
    [[ -f "$file" ]] || die "path contract missing: $file"
    require_cmds jq
    jq empty "$file" 2>/dev/null || die "path contract is not valid JSON: $file"

    [[ "$(jq -r '.schema // 0' "$file")" == "1" ]] || die "unsupported path contract schema"

    while IFS=$'\t' read -r key value; do
        P["$key"]="$value"
    done < <(jq -r '
        {
          stack: .stack, environment: .environment, short: .short,
          image_prefix: .image_prefix,
          root: .vps.root, stack_dir: .vps.stack_dir,
          images_dir: .vps.images_dir,
          compose_file: .vps.compose_file, compose_name: .vps.compose_name,
          env_file: .vps.env_file, env_example: .vps.env_example,
          version_file: .vps.version_file, version_name: .vps.version_name,
          manifest_file: .vps.manifest_file, checksums_file: .vps.checksums_file,
          market_data_dir: (.vps.market_data_dir // ""),
          docker: .vps.docker,
          backend_container: .vps.backend_container,
          frontend_container: .vps.frontend_container,
          compose_project: .vps.compose_project, lock_file: .vps.lock_file,
          backup_root: .backup.root,
          rollback_root: .backup.rollback_root,
          rollback_images: .backup.rollback_images,
          logs_root: .backup.logs_root,
          deploy_log: .backup.deploy_log, app_log: .backup.app_log,
          health_settle: (.health.settle_seconds // 180 | tostring),
          capture_open: (.capture.default_open // "09:00"),
          capture_close: (.capture.default_close // "15:30"),
          capture_timezone: (.capture.default_timezone // "Asia/Kolkata"),
          keep_releases: (.retention.keep_releases | tostring)
        } | to_entries[] | [.key, (.value // "")] | @tsv' "$file")

    [[ -n "${P[stack_dir]}" ]] || die "contract has no vps.stack_dir"
    dd_assert_safe_dir "${P[stack_dir]}"
    dd_assert_safe_dir "${P[rollback_images]}"
}

# Refuse a path that could be the filesystem root or escape by traversal. The
# rollback tree gets pruned, so this is load-bearing.
dd_assert_safe_dir() {
    local dir="${1:-}" depth
    [[ -n "$dir" ]]                     || die "refusing an empty path"
    [[ "$dir" == /* ]]                  || die "refusing a relative path: $dir"
    [[ "$dir" != *".."* ]]              || die "refusing a path with ..: $dir"
    [[ "$dir" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "refusing an unsafe path: $dir"
    depth="$(printf '%s' "${dir//[!\/]/}" | wc -c)"
    (( depth >= 3 )) || die "refusing a shallow path: $dir"
}

docker_bin() { printf '%s\n' "${P[docker]:-docker}"; }

# dd_inspect <container> <format> — one field, or nothing and status 1 when the
# container does not exist. Not "inspect || echo missing": for a missing
# container docker prints an empty line before failing.
dd_inspect() {
    local out
    out="$("$(docker_bin)" inspect -f "$2" "$1" 2>/dev/null)" || return 1
    printf '%s' "$out"
}

dd_image_tag() { printf '%s-%s:%s\n' "${P[image_prefix]}" "$1" "$2"; }

# compose <args...> — docker compose, always with this stack's project and file.
# DD_VERSION picks the image tags; .env beside the compose file supplies the
# rest, read by Docker alone.
compose() {
    ( cd "${P[stack_dir]}" && \
      DD_VERSION="${DD_VERSION_FOR_COMPOSE:?DD_VERSION_FOR_COMPOSE not set}" \
      "$(docker_bin)" compose \
        --project-name "${P[compose_project]}" \
        --file "${P[compose_file]}" "$@" )
}

# ── locking ─────────────────────────────────────────────────────────────────
# One lock shared by deploy, reload and rollback, so they can never interleave.
dd_lock() {
    require_cmds flock
    exec {DD_LOCK_FD}>"${P[lock_file]}" \
        || die "cannot open lock file ${P[lock_file]}"
    flock -n "$DD_LOCK_FD" \
        || die "another deploy or rollback holds ${P[lock_file]}"
    ok "acquired ${P[lock_file]}"
}

# ── logging ─────────────────────────────────────────────────────────────────
dd_open_log() {
    local kind="$1" dir
    case "$kind" in
        deploy) dir="${P[deploy_log]}" ;;
        *)      dir="${P[app_log]}" ;;
    esac
    mkdir -p "$dir" 2>/dev/null || true
    DD_LOG_FILE="$dir/$(date -u +%Y%m%dT%H%M%SZ)-$kind.log"
    : > "$DD_LOG_FILE" 2>/dev/null || DD_LOG_FILE=''
    [[ -n "$DD_LOG_FILE" ]] && ok "logging to $DD_LOG_FILE"
}

# ── preconditions ───────────────────────────────────────────────────────────
dd_assert_writable() {
    local dir
    for dir in "$@"; do
        [[ -n "$dir" ]] || continue
        mkdir -p "$dir" 2>/dev/null || die "cannot create $dir"
        [[ -w "$dir" ]] || die "not writable: $dir"
    done
}

dd_assert_backup_tree() {
    dd_assert_writable "${P[backup_root]}" "${P[rollback_root]}" \
                       "${P[rollback_images]}" "${P[logs_root]}" \
                       "${P[deploy_log]}" "${P[app_log]}"
    ok "backup and log tree writable"
}

# dd_assert_space <dir> <need MiB>
dd_assert_space() {
    local dir="$1" need="$2" avail
    avail="$(df -BM --output=avail "$dir" 2>/dev/null | tail -n1 | tr -dc '0-9')"
    [[ -n "$avail" ]] || { warn "cannot determine free space on $dir"; return 0; }
    (( avail >= need )) || die "insufficient space on $dir: ${avail}MiB free, need ${need}MiB"
    info "$dir has ${avail}MiB free (need ${need}MiB)"
}

# Twice the incoming archive size, so the outgoing release can be archived
# alongside it before the new one is loaded.
dd_required_space_mib() {
    local total=0
    if [[ -d "${P[images_dir]}" ]]; then
        total="$(du -sm "${P[images_dir]}" 2>/dev/null | cut -f1 || echo 0)"
    fi
    printf '%s\n' "$(( total * 2 + 512 ))"
}

dd_assert_docker() {
    "$(docker_bin)" info >/dev/null 2>&1 || die "docker is not usable by $(id -un)"
    "$(docker_bin)" compose version >/dev/null 2>&1 || die "docker compose plugin missing"
    ok "docker and compose available"
}

# The app will not start without its settings, so a missing env file is worth
# failing fast on. That is the ONLY thing this checks.
dd_assert_env() {
    [[ -e "${P[env_file]}" ]] \
        || die "missing ${P[env_file]} — place it yourself, then redeploy"
    ok "env file present (contents not inspected)"
}

dd_validate_compose() {
    compose config --quiet >/dev/null 2>&1 || {
        compose config --quiet 2>&1 | tail -20 >&2
        die "compose file is not valid for this release"
    }
    ok "compose file validates"
}

# ── capture guards ──────────────────────────────────────────────────────────
# A deploy restarts the backend, and a restart during the session loses ticks
# that can never be captured again. These guards run against the RUNNING
# backend: with none running, nothing is being captured and they pass.

dd_backend_running() {
    [[ "$(dd_inspect "${P[backend_container]}" '{{.State.Status}}')" == running ]]
}

# dd_backend_python <code> [args...] — run Python inside the running backend,
# where its own environment holds HTTP_PORT and the maintenance token.
dd_backend_python() {
    local code="$1"; shift
    "$(docker_bin)" exec -i "${P[backend_container]}" python - "$@" <<<"$code"
}

# dd_backend_env <key> — one variable from the running backend's environment.
dd_backend_env() {
    "$(docker_bin)" exec "${P[backend_container]}" printenv "$1" 2>/dev/null || true
}

# dd_in_capture_window — true while the clock is inside MARKET_OPEN..MARKET_CLOSE
# as the running backend has them (the contract's defaults otherwise).
dd_in_capture_window() {
    local open close tz now open_min close_min now_min
    open="$(dd_backend_env MARKET_OPEN)";      open="${open:-${P[capture_open]}}"
    close="$(dd_backend_env MARKET_CLOSE)";    close="${close:-${P[capture_close]}}"
    tz="$(dd_backend_env MARKET_TIMEZONE)";    tz="${tz:-${P[capture_timezone]}}"
    [[ "$open" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ && "$close" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] \
        || die "the backend's MARKET_OPEN/MARKET_CLOSE are not HH:MM ($open, $close)"
    now="${DD_TEST_HHMM:-$(TZ="$tz" date +%H:%M)}"
    open_min=$((10#${open%:*} * 60 + 10#${open#*:}))
    close_min=$((10#${close%:*} * 60 + 10#${close#*:}))
    now_min=$((10#${now%:*} * 60 + 10#${now#*:}))
    DD_WINDOW="$open-$close $tz"
    if (( open_min < close_min )); then
        (( now_min >= open_min && now_min < close_min ))
    else
        (( now_min >= open_min || now_min < close_min ))
    fi
}

# dd_capture_state — running, stopped, or unknown, from /api/capture/status.
dd_capture_state() {
    dd_backend_python '
import json, os, urllib.request
url = "http://127.0.0.1:%s/api/capture/status" % os.environ["HTTP_PORT"]
body = json.load(urllib.request.urlopen(url, timeout=5))
print("running" if body.get("running") is True else "stopped")
' 2>/dev/null || printf 'unknown\n'
}

# dd_guard_capture <force> — refuse to restart the backend while it captures.
# --force bypasses the window and the running check (for a stuck stack that
# must be replaced now); the writers are still drained if the backend answers.
dd_guard_capture() {
    local force="$1" state
    if ! dd_backend_running; then
        info "no backend running — nothing is being captured"
        return 0
    fi
    if dd_in_capture_window; then
        [[ "$force" == true ]] || die "refusing during the capture window ($DD_WINDOW); use --force for a stuck stack"
        warn "FORCE: inside the capture window ($DD_WINDOW)"
    else
        ok "outside the capture window ($DD_WINDOW)"
    fi
    state="$(dd_capture_state)"
    case "$state" in
        stopped) ok "capture is stopped" ;;
        running)
            [[ "$force" == true ]] || die "capture is running; wait for the close and EOD before restarting"
            warn "FORCE: capture is running; draining its writers before the swap" ;;
        *)
            [[ "$force" == true ]] || die "cannot read the capture state from the backend; refusing to restart"
            warn "FORCE: capture state unknown; proceeding" ;;
    esac
}

# dd_drain_writers <force> — take the release-maintenance lease so the capture
# writers flush and pause before the containers are replaced. The lease expires
# by itself (RELEASE_MAINTENANCE_TTL_SECONDS), so a crashed deploy cannot block
# capture forever; dd_release_lease ends it early on the way out.
dd_drain_writers() {
    local force="$1" lease
    dd_backend_running || return 0
    if lease="$(dd_backend_python '
import json, os, urllib.request
url = "http://127.0.0.1:%s/api/capture/maintenance" % os.environ["HTTP_PORT"]
request = urllib.request.Request(url, method="POST",
    headers={"X-Release-Maintenance-Token": os.environ.get("RELEASE_MAINTENANCE_TOKEN", "")})
print(json.load(urllib.request.urlopen(request, timeout=20))["lease_id"])
' 2>/dev/null)" && [[ "$lease" =~ ^[A-Za-z0-9_-]{8,128}$ ]]; then
        DD_LEASE_ID="$lease"
        ok "capture writers drained (lease ${DD_LEASE_ID:0:8}…)"
        return 0
    fi
    [[ "$force" == true ]] || die "could not take the capture maintenance lease; check RELEASE_MAINTENANCE_TOKEN"
    warn "FORCE: no maintenance lease (backend unresponsive?); proceeding without a clean drain"
}

dd_release_lease() {
    [[ -n "$DD_LEASE_ID" ]] || return 0
    dd_backend_running || return 0
    dd_backend_python '
import os, sys, urllib.request
url = "http://127.0.0.1:%s/api/capture/maintenance/%s" % (os.environ["HTTP_PORT"], sys.argv[1])
request = urllib.request.Request(url, method="DELETE",
    headers={"X-Release-Maintenance-Token": os.environ.get("RELEASE_MAINTENANCE_TOKEN", "")})
urllib.request.urlopen(request, timeout=10)
' "$DD_LEASE_ID" >/dev/null 2>&1 && info "maintenance lease released"
    DD_LEASE_ID=''
}

# ── version bookkeeping ─────────────────────────────────────────────────────
dd_current_version() {
    [[ -f "${P[version_file]}" ]] || return 0
    jq -r '.version // empty' "${P[version_file]}" 2>/dev/null || true
}

dd_incoming_version() {
    [[ -f "${P[manifest_file]}" ]] || die "manifest.json missing — ship a bundle first"
    local v; v="$(jq -r '.version // empty' "${P[manifest_file]}" 2>/dev/null || true)"
    [[ -n "$v" ]] || die "manifest.json has no version (a bundle from the old pipeline?)"
    [[ "$v" =~ ^[A-Za-z0-9._-]+$ ]] || die "manifest.json names an unsafe version: $v"
    printf '%s\n' "$v"
}

# dd_write_version <version> <previous> <status> [attempted]
#
# A failed deploy must not rewrite history: .version keeps pointing at what is
# actually running and the failed release is recorded separately, so a retry is
# never told the failed version is already deployed.
dd_write_version() {
    local version="$1" previous="$2" status="$3" attempted="${4:-}" tmp
    tmp="$(mktemp "${P[version_file]}.XXXXXX")" || die "cannot write version file"
    jq -n --arg v "$version" --arg p "$previous" --arg s "$status" \
          --arg a "$attempted" --arg at "$(date -Is)" --arg stack "${P[stack]}" \
        '{stack: $stack, version: $v, previous: $p, status: $s,
          last_attempted: $a, updated_at: $at}' > "$tmp" \
        || die "cannot serialise version file"
    chmod 644 "$tmp" 2>/dev/null || true
    mv "$tmp" "${P[version_file]}"
    ok "recorded $status: ${version:-<none>}"
}

# ── images ──────────────────────────────────────────────────────────────────
# dd_images — one "key<TAB>archive" line per declared image.
dd_images() {
    jq -r '.images[] | [.key, .archive] | @tsv' "${P[paths_file]:-${P[stack_dir]}/paths.json}"
}

dd_verify_checksums() {
    [[ -f "${P[checksums_file]}" ]] || die "checksums.sha256 missing"
    ( cd "${P[stack_dir]}" && sha256sum -c --quiet "${P[checksums_file]}" ) \
        || die "checksum verification failed — the upload is corrupt, re-ship"
    ok "checksums verified on arrival"
}

# dd_archive_current_images <dest> <version>
#
# Saves the running images and the compose file that started them, so a rollback
# restores a matched pair rather than an old image under a new compose. Each
# archive is written under a temporary name first: a save cut off half-way must
# never look like a complete rollback target.
dd_archive_current_images() {
    local dest="$1" version="$2" key archive tag
    # A complete archive is left exactly as it is: rewriting it would change
    # what it restores and its date, which orders rollback and pruning.
    if [[ -d "$dest" ]] && dd_rollback_complete "$dest"; then
        ok "$version is already archived in $dest"
        return 0
    fi
    dd_assert_writable "$dest"
    while IFS=$'\t' read -r key archive; do
        tag="$(dd_image_tag "$key" "$version")"
        [[ -f "$dest/$archive" ]] && continue
        if ! "$(docker_bin)" image inspect "$tag" >/dev/null 2>&1; then
            warn "running image $tag not present locally — cannot archive it"
            continue
        fi
        "$(docker_bin)" save "$tag" | gzip -n > "$dest/$archive.partial" \
            || { rm -f -- "$dest/$archive.partial"; die "failed to archive $tag"; }
        mv -- "$dest/$archive.partial" "$dest/$archive"
        info "archived $archive"
    done < <(dd_images)
    [[ -f "${P[compose_file]}" ]] && cp "${P[compose_file]}" "$dest/${P[compose_name]}"
    printf '%s\n' "$version" > "$dest/version.txt"
    ( cd "$dest" && find . -maxdepth 1 -type f ! -name checksums.sha256 -print0 \
        | sort -z | xargs -0 -r sha256sum > checksums.sha256 ) || true
    ok "outgoing release archived to $dest"
}

dd_load_images() {
    local key archive path
    while IFS=$'\t' read -r key archive; do
        path="${P[images_dir]}/$archive"
        [[ -f "$path" ]] || die "image archive missing: $path"
        gzip -dc "$path" | "$(docker_bin)" image load >/dev/null \
            || die "failed to load $archive"
        info "loaded $archive"
    done < <(dd_images)
    ok "images loaded"
}

dd_assert_images_present() {
    local version="$1" key archive tag
    while IFS=$'\t' read -r key archive; do
        tag="$(dd_image_tag "$key" "$version")"
        "$(docker_bin)" image inspect "$tag" >/dev/null 2>&1 \
            || die "image $tag is not present after load"
    done < <(dd_images)
    ok "expected image tags present for $version"
}

# dd_rollback_complete <dir> — every declared image archive is present. Older
# archives (from the pre-contract pipeline) carry only the two image archives
# and version.txt, no checksums or compose file, and are still usable.
dd_rollback_complete() {
    local dir="$1" key archive
    while IFS=$'\t' read -r key archive; do
        [[ -f "$dir/$archive" ]] || return 1
    done < <(dd_images)
}

dd_rollback_verify() {
    local dir="$1"
    [[ -d "$dir" ]] || die "no rollback archive at $dir"
    dd_rollback_complete "$dir" || die "rollback archive at $dir is missing an image archive"
    [[ -f "$dir/checksums.sha256" ]] || { warn "rollback archive has no checksums (an older archive)"; return 0; }
    ( cd "$dir" && sha256sum -c --quiet checksums.sha256 ) \
        || die "rollback archive at $dir failed checksum verification"
    ok "rollback archive verified"
}

# dd_restore_images <dir> — load every image archive of a rollback target.
dd_restore_images() {
    local dir="$1" key archive
    while IFS=$'\t' read -r key archive; do
        [[ -f "$dir/$archive" ]] || return 1
        gzip -dc "$dir/$archive" | "$(docker_bin)" image load >/dev/null 2>&1 || return 1
        info "restored $archive"
    done < <(dd_images)
}

# ── health ──────────────────────────────────────────────────────────────────
# dd_health_gate — both containers must reach Docker's own "healthy" within the
# settle time, without restarting. The compose healthchecks probe /health and
# /login from inside the containers, so this works whatever HOST_BIND_ADDRESS
# the .env publishes on.
dd_health_gate() {
    local settle="${P[health_settle]:-180}" waited=0 name state health pending
    local -a names=("${P[backend_container]}" "${P[frontend_container]}")
    info "waiting up to ${settle}s for both containers to report healthy"
    while (( waited < settle )); do
        pending=0
        for name in "${names[@]}"; do
            state="$(dd_inspect "$name" '{{.State.Status}}')" || state=missing
            health="$(dd_inspect "$name" '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" || health=missing
            case "$state:$health" in
                running:healthy) : ;;
                running:starting|missing:*|created:*) pending=$((pending + 1)) ;;
                running:unhealthy) warn "$name is unhealthy"; return 1 ;;
                running:none) warn "$name has no healthcheck"; return 1 ;;
                *) warn "$name is $state"; return 1 ;;
            esac
        done
        if (( pending == 0 )); then
            for name in "${names[@]}"; do
                (( "$(dd_inspect "$name" '{{.RestartCount}}' || echo 0)" == 0 )) \
                    || { warn "$name restarted while starting"; return 1; }
            done
            ok "backend and frontend healthy after ${waited}s"
            return 0
        fi
        sleep 3
        waited=$(( waited + 3 ))
    done
    warn "the containers were not healthy within ${settle}s"
    return 1
}

dd_capture_app_log() {
    local label="$1" dest name
    mkdir -p "${P[app_log]}" 2>/dev/null || return 0
    for name in "${P[backend_container]}" "${P[frontend_container]}"; do
        dest="${P[app_log]}/$(date -u +%Y%m%dT%H%M%SZ)-$label-$name.log"
        "$(docker_bin)" logs --tail 400 "$name" > "$dest" 2>&1 || true
        [[ -s "$dest" ]] && info "container log captured to $dest"
    done
}

# ── retention ───────────────────────────────────────────────────────────────
# dd_prune_rollbacks <dir> <keep>
#
# Only ever removes immediate subdirectories of a path already proven safe by
# dd_assert_safe_dir. Sorted by modification time, so a dev label, a stable
# label and an older "v"-prefixed one compare by when they were archived rather
# than by spelling.
dd_prune_rollbacks() {
    local dir="$1" keep="$2" count victim
    [[ -d "$dir" ]] || return 0
    dd_assert_safe_dir "$dir"
    count="$(find "$dir" -mindepth 1 -maxdepth 1 -type d | wc -l)"
    (( count > keep )) || return 0
    while IFS= read -r victim; do
        [[ -n "$victim" ]] || continue
        rm -rf -- "${dir:?}/$victim" && info "pruned rollback $victim"
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%f\n' \
             | sort -n | head -n $(( count - keep )) | cut -f2)
}

# dd_prune_old_images <keep-version...> — remove this stack's own image tags
# that are neither running nor a rollback target. Scoped to the image prefix,
# so another stack's images on this shared box are never touched.
dd_prune_old_images() {
    local tag version keepers=" $* "
    while IFS= read -r tag; do
        [[ -n "$tag" ]] || continue
        version="${tag##*:}"
        [[ "$keepers" == *" $version "* ]] && continue
        "$(docker_bin)" image rm "$tag" >/dev/null 2>&1 && info "removed image $tag"
    done < <("$(docker_bin)" image ls --format '{{.Repository}}:{{.Tag}}' \
             | grep -E "^${P[image_prefix]}-(backend|frontend):" || true)
}

dd_rollback_versions() {
    find "${P[rollback_images]}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null || true
}

dd_summary() {
    local title="$1" pair; shift
    printf '\n%s══ %s ══%s\n' "$_c_bold" "$title" "$_c_rst"
    for pair in "$@"; do printf '     %-14s %s\n' "${pair%%=*}" "${pair#*=}"; done
    printf '\n'
    log "SUMMARY $title $*"
}
