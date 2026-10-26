#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_ship.sh — SHIP stage. Runs on this computer; deploys nothing itself.
#
#   THIS MODULE                      THE VPS-NATIVE SCRIPT
#   ──────────────────────────       ──────────────────────────────────
#   verify the bundle                acquire the deploy lock
#   verify the VPS is reachable      verify checksums again, on arrival
#   verify remote preconditions      capture guard, archive the release
#   upload artifacts + scripts       docker load, drain writers, compose up
#   invoke <stack>_deploy.sh  ───▶   health-gate and record the version
#   stream its output back           prune old rollbacks and images
#
# This module runs NO docker command against the VPS. Every container operation
# happens inside the VPS-native script, the one place holding the lock.
#
# Transfer is rsync over SSH, writing ONLY the files this pipeline owns, by name,
# with no --delete anywhere. Nothing removes a remote file it did not put there,
# so .env, MARKET_DATA (which lives in the stack directory) and the archive are
# untouched.
# ─────────────────────────────────────────────────────────────────────────────

# op_ship <paths.json> <stack> <deploy|ship-only|force> [--bundle DIR] [--yes] [--skip-checks]
op_ship() {
    local paths_file="$1" stack="$2" mode="$3"; shift 3
    local bundle_arg='' assume_yes=false
    local -a remote_args=()
    case "$mode" in
        deploy|ship-only) : ;;
        force) remote_args+=(--force) ;;
        *) err "unknown ship mode: $mode"; exit 1 ;;
    esac
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --bundle)      bundle_arg="${2:-}"; shift 2 ;;
            --yes|-y)      assume_yes=true; remote_args+=(--yes); shift ;;
            --skip-checks) remote_args+=(--skip-checks); shift ;;
            *) err "unknown ship option: $1"; exit 1 ;;
        esac
    done

    local c
    for c in ssh rsync jq sha256sum; do
        command -v "$c" >/dev/null || { err "$c is required"; exit 1; }
    done

    local remote_dir backup_root
    remote_dir="$(paths_get "$paths_file" .vps.stack_dir)" || exit 1
    backup_root="$(paths_get "$paths_file" .backup.root)" || exit 1
    assert_safe_remote_dir "$remote_dir" || exit 1

    banner "SHIP · $stack · $mode"

    # ── 1. bundle ───────────────────────────────────────────────────────────
    section "1/6  BUNDLE"
    local bundle
    if [[ -n "$bundle_arg" ]]; then
        bundle="$(cd "$bundle_arg" 2>/dev/null && pwd)" || { err "no such bundle: $bundle_arg"; exit 1; }
    else
        bundle="$(bundle_path_newest "$BUILD_DIR/$(host_build_dir "$stack")")"
        [[ -n "$bundle" ]] || { err "no bundle staged — run Export first"; exit 1; }
    fi

    local compose_name deploy_name rollback_name guide_name f
    compose_name="$(stack_attr "$stack" compose)"
    deploy_name="$(stack_attr "$stack" deploy)"
    rollback_name="$(stack_attr "$stack" rollback)"
    guide_name="$(stack_attr "$stack" guide)"
    for f in manifest.json paths.json checksums.sha256 "$compose_name" "$deploy_name" \
             "$rollback_name" _dd_lib.sh _dd_deploy.sh _dd_rollback.sh .env.example; do
        [[ -f "$bundle/$f" ]] || { err "bundle is incomplete, missing: $f"; exit 1; }
    done

    ship_bind_contract "$stack" "$paths_file" "$bundle"

    local bundle_host version kind dirty
    bundle_host="$(jq -r '.host // empty' "$bundle/manifest.json")"
    if [[ -n "$bundle_host" && "$DD_HOST" != '(DD_SSH_ALIAS)' && "$bundle_host" != "$DD_HOST" ]]; then
        err "this bundle was built for $bundle_host, and the target is $DD_HOST"; exit 1
    fi
    version="$(jq -r '.version // empty' "$bundle/manifest.json")"
    kind="$(jq -r '.kind // "unknown"' "$bundle/manifest.json")"
    dirty="$(jq -r '.git_dirty // false' "$bundle/manifest.json")"
    [[ -n "$version" ]] || { err "bundle manifest has no version"; exit 1; }
    [[ "$dirty" != true ]] || warn "this bundle was built from a DIRTY tree: $version is not reproducible from git"

    field "bundle"  "${bundle#"$ROOT_DIR"/}"
    field "version" "$version ($kind)"
    field "host"    "$(host_describe)"
    field "remote"  "${DD_SSH_ALIAS}:${remote_dir}"

    step "verifying bundle checksums locally"
    local key sha archive actual
    while read -r key sha archive; do
        [[ -n "$key" ]] || continue
        [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { err "no checksum recorded for image $key"; exit 1; }
        actual="$(sha256sum "$bundle/$archive" | cut -d' ' -f1)"
        [[ "$actual" == "$sha" ]] || { err "local checksum mismatch for $archive"; exit 1; }
    done < <(jq -r '.images | to_entries[] | [.key, .value.sha256, .value.archive] | @tsv' "$bundle/manifest.json")
    ok "bundle checksums verified locally"

    # ── 2. remote preflight ─────────────────────────────────────────────────
    section "2/6  REMOTE PREFLIGHT"
    ship_preflight "$remote_dir" "$backup_root" "$bundle" "$mode"

    # ── 3. confirm ──────────────────────────────────────────────────────────
    if [[ "$assume_yes" != true && "$UI_INTERACTIVE" == true ]]; then
        printf '\n'
        confirm "Ship $version to $(host_describe)?" || { warn "aborted"; exit 0; }
    fi

    # ── 4. upload ───────────────────────────────────────────────────────────
    section "3/6  UPLOAD"
    ship_upload "$bundle" "$remote_dir" "$compose_name" "$deploy_name" "$rollback_name" "$guide_name"

    if [[ "$mode" == ship-only ]]; then
        banner "SHIPPED (not deployed)"
        field "version" "$version"
        printf '\n   Deploy it from the Ship + Deploy menu, or on the VPS with:\n'
        printf '     ssh %s "cd %s && ./%s"\n\n' "$DD_SSH_ALIAS" "$remote_dir" "$deploy_name"
        exit 0
    fi

    # ── 5. remote deploy ────────────────────────────────────────────────────
    section "4/6  REMOTE DEPLOY" "all docker work happens on the VPS from here"
    local rc=0
    ship_remote_run "$remote_dir" "$deploy_name" "${remote_args[@]}" || rc=$?

    # ── 6. reconcile ────────────────────────────────────────────────────────
    section "5/6  RECONCILE"
    local version_name deployed status
    version_name="$(stack_attr "$stack" version_file)"
    deployed="$(dd_ssh "jq -r '.version // empty' '$remote_dir/$version_name' 2>/dev/null" || true)"
    status="$(dd_ssh "jq -r '.status // empty' '$remote_dir/$version_name' 2>/dev/null" || true)"
    ship_record_ledger "$stack" "$version" "$deployed" "$status"
    field "shipped"  "$version"
    field "deployed" "${deployed:-<unknown>}"
    field "status"   "${status:-<unknown>}"

    # Measured whether or not the deploy succeeded: a disk-full or memory-starved
    # box is exactly the failure this explains.
    section "6/6  RESOURCES"
    resource_report

    if (( rc != 0 )); then
        err "remote deploy exited with status $rc — see View deploy logs"
        exit "$rc"
    fi
    [[ "$deployed" == "$version" ]] || { warn "remote reports '$deployed' but we shipped '$version'"; exit 1; }
    banner "DEPLOYED"
    field "version" "$version"
    field "status"  "${status:-active}"
}

# Bind the bundle to the tracked contract before any upload: its copy must
# validate, match the digest its own manifest recorded, and be byte-identical to
# the tracked file. A contract edited after export fails closed here.
ship_bind_contract() {
    local stack="$1" paths_file="$2" bundle="$3" bundle_sha manifest_sha
    step "binding the bundle to the tracked path contract"
    paths_validate "$stack" "$bundle/paths.json" \
        || { err "bundle paths.json is not a valid $stack contract — re-export"; exit 1; }
    bundle_sha="$(sha256sum "$bundle/paths.json" | cut -d' ' -f1)"
    manifest_sha="$(jq -r '.paths.sha256 // empty' "$bundle/manifest.json")"
    [[ -z "$manifest_sha" || "$manifest_sha" == "$bundle_sha" ]] \
        || { err "bundle paths.json was altered after export — re-export"; exit 1; }
    [[ "$bundle_sha" == "$(sha256sum "$paths_file" | cut -d' ' -f1)" ]] \
        || { err "the path contract changed after this bundle was exported — re-export"; exit 1; }
    ok "bundle path contract matches the tracked contract"
}

ship_preflight() {
    local remote_dir="$1" backup_root="$2" bundle="$3" mode="$4" preflight
    step "checking SSH connectivity"
    dd_ssh true 2>/dev/null || { err "cannot reach $DD_SSH_ALIAS over SSH"; exit 1; }
    ok "SSH ok"

    # One read-only round trip that reports everything needed before uploading.
    preflight="$(dd_ssh "bash -s -- '$remote_dir' '$backup_root'" <<'REMOTE' || true
set -u
stack_dir="$1"; backup_root="$2"
yn() { if "$@" >/dev/null 2>&1; then echo yes; else echo no; fi; }
echo "stack_dir_writable=$(yn test -w "$stack_dir")"
echo "backup_writable=$(yn test -w "$backup_root")"
# Existence only. .env belongs to the operator.
echo "env_present=$(yn test -e "$stack_dir/.env")"
echo "docker_ok=$(yn docker info)"
echo "compose_ok=$(yn docker compose version)"
for c in rsync flock jq gzip; do echo "${c}_ok=$(yn command -v "$c")"; done
echo "disk_avail_mib=$(df -BM --output=avail "$stack_dir" 2>/dev/null | tail -n1 | tr -dc '0-9')"
# The pipeline before the contract kept its rollbacks here and its own scripts
# in the stack dir; both must be gone (APP_GUIDE.md, the one-time move).
echo "legacy_rollbacks=$(yn test -d "$(dirname "$backup_root")/ROLLBACKS")"
echo "legacy_scripts=$(yn test -e "$stack_dir/deploy.sh" -o -e "$stack_dir/rollback.sh")"
REMOTE
)"
    get_flag() { sed -n "s/^$1=//p" <<<"$preflight" | tail -n1; }

    local f
    for f in docker_ok compose_ok rsync_ok flock_ok jq_ok gzip_ok; do
        [[ "$(get_flag "$f")" == yes ]] || { err "remote preflight failed: ${f%_ok} is unavailable"; exit 1; }
    done
    ok "docker, compose, rsync, flock, jq and gzip available"

    [[ "$(get_flag stack_dir_writable)" == yes ]] || { err "$remote_dir is missing or not writable — run Provision"; exit 1; }
    [[ "$(get_flag backup_writable)" == yes ]] || { err "$backup_root is missing or not writable — run Provision"; exit 1; }
    ok "remote stack and backup trees writable"

    if [[ "$(get_flag env_present)" != yes ]]; then
        warn "remote $remote_dir/.env is not present"
        [[ "$mode" == ship-only ]] || {
            err "the app cannot start without it. Place it yourself (mode 600); nothing here writes it."
            exit 1; }
    fi

    if [[ "$(get_flag legacy_rollbacks)" == yes || "$(get_flag legacy_scripts)" == yes ]]; then
        warn "the one-time move to the contract layout is not done (APP_GUIDE.md, \"Moving from the old layout\")"
        [[ "$(get_flag legacy_rollbacks)" == yes ]] && info "the old rollbacks are still in $(dirname "$backup_root")/ROLLBACKS"
        [[ "$(get_flag legacy_scripts)" == yes ]] && info "the old deploy.sh / rollback.sh are still in $remote_dir"
        [[ "$mode" == ship-only ]] || { err "refusing to deploy until it is done"; exit 1; }
    fi

    local avail bundle_mib need
    avail="$(get_flag disk_avail_mib)"
    bundle_mib="$(du -sm "$bundle" | cut -f1)"
    need=$(( bundle_mib * 3 ))
    if [[ -n "$avail" ]] && (( avail < need )); then
        err "insufficient remote disk: ${avail}MiB free, need ~${need}MiB"; exit 1
    fi
    field "remote disk" "${avail:-?}MiB free (bundle ${bundle_mib}MiB)"
}

ship_upload() {
    local bundle="$1" remote_dir="$2" compose_name="$3" deploy_name="$4" rollback_name="$5" guide_name="$6"
    # --checksum re-verifies content rather than trusting size+mtime. No
    # --delete, and --exclude='/.env' is the hard guarantee that this pipeline
    # never writes the operator's credentials.
    local -a opts=(-az --checksum --human-readable --partial --chmod=F644,D755 --exclude='/.env')
    local rsync_ssh
    dd_ssh_opts || exit 1
    printf -v rsync_ssh '%q ' ssh "${DD_SSH_OPTS[@]}"

    step "uploading release metadata"
    local -a metadata=("$bundle/manifest.json" "$bundle/paths.json" "$bundle/checksums.sha256"
                       "$bundle/$compose_name" "$bundle/.env.example")
    [[ -f "$bundle/$guide_name" ]] && metadata+=("$bundle/$guide_name")
    rsync "${opts[@]}" -e "$rsync_ssh" "${metadata[@]}" "${DD_SSH_ALIAS}:${remote_dir}/" \
        || { err "failed to upload release metadata"; exit 1; }

    step "uploading VPS-native scripts"
    rsync -az --checksum --chmod=F755,D755 -e "$rsync_ssh" \
        "$bundle/$deploy_name" "$bundle/$rollback_name" \
        "$bundle/_dd_lib.sh" "$bundle/_dd_deploy.sh" "$bundle/_dd_rollback.sh" \
        "${DD_SSH_ALIAS}:${remote_dir}/" || { err "failed to upload native scripts"; exit 1; }

    step "uploading image archives ($(du -sh "$bundle/images" | cut -f1))"
    dd_ssh "mkdir -p '$remote_dir/images'" || { err "cannot create the remote images dir"; exit 1; }
    rsync "${opts[@]}" --info=progress2 -e "$rsync_ssh" \
        "$bundle/images/" "${DD_SSH_ALIAS}:${remote_dir}/images/" \
        || { err "failed to upload image archives"; exit 1; }

    step "verifying uploads on the VPS"
    local verify
    verify="$(dd_ssh "cd '$remote_dir' && sha256sum -c --quiet checksums.sha256 2>&1 && echo VERIFY_OK" || true)"
    grep -q VERIFY_OK <<<"$verify" || {
        err "remote checksum verification FAILED after upload"; head -20 <<<"$verify" >&2; exit 1; }
    ok "remote checksums match — upload intact"
}

# ship_remote_run <remote_dir> <script> [args...] — run a VPS-native script with
# its output streamed live. -t only when this side has a terminal, so a prompt
# on the VPS can be answered; without one the remote script needs --yes.
ship_remote_run() {
    local remote_dir="$1" script="$2"; shift 2
    local -a tty=()
    dd_ssh_opts || return 1
    [[ "$UI_INTERACTIVE" == true ]] && tty=(-t)
    printf '\n'
    ssh "${tty[@]}" "${DD_SSH_OPTS[@]}" "$DD_SSH_ALIAS" \
        "cd '$remote_dir' && ./'$script' ${*:-}"
}

ship_record_ledger() {
    local stack="$1" built="$2" deployed="$3" status="$4" ledger="$RM_DIR/state/versions.json" tmp
    mkdir -p "$RM_DIR/state"
    [[ -s "$ledger" ]] || printf '{}\n' > "$ledger"
    tmp="$(mktemp "${ledger}.XXXXXX")"
    jq --arg s "$stack" --arg b "$built" --arg d "${deployed:-}" --arg st "${status:-unknown}" \
       --arg at "$(date -Is)" '.[$s] = {built: $b, deployed: $d, status: $st, shipped_at: $at}' \
       "$ledger" > "$tmp" && mv "$tmp" "$ledger"
}
