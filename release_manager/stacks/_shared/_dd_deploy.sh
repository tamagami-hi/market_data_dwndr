#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# _dd_deploy.sh — the VPS-native deployment flow.
#
# Runs ON THE VPS. Sourced by data_downloader_deploy.sh, which supplies only
# identity and policy. Everything else is driven by paths.json.
#
# Contract with the operator machine: by the time this runs, status.sh has
# placed manifest.json, paths.json, checksums.sha256, the compose file and
# images/*.tar.gz into the stack directory. This script owns every docker
# command from here on; the operator side runs none.
#
# It never touches .env, MARKET_DATA or the archive: it writes only the files
# the pipeline owns and the backup tree in the contract.
# ─────────────────────────────────────────────────────────────────────────────

dd_deploy_main() {
    local paths_file="$1"; shift

    local ASSUME_YES=false SKIP_CHECKS=false FORCE=false RELOAD=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --yes|-y)      ASSUME_YES=true; shift ;;
            --skip-checks) SKIP_CHECKS=true; shift ;;
            --force)       FORCE=true; shift ;;
            --reload)      RELOAD=true; shift ;;
            --help|-h)     dd_deploy_usage; return 0 ;;
            *) printf 'Unknown argument: %s\n' "$1" >&2; dd_deploy_usage >&2; return 1 ;;
        esac
    done

    require_cmds jq sha256sum gzip find sort
    dd_load_paths "$paths_file"
    P[paths_file]="$paths_file"

    if [[ "$RELOAD" == true ]]; then
        dd_reload_main "$ASSUME_YES" "$FORCE"
        return
    fi

    printf '\n%s═══ data downloader deploy · %s (%s) ═══%s\n' \
        "$_c_bold" "${P[stack]}" "${P[environment]}" "$_c_rst"

    step "1/13 acquire deployment lock"
    dd_lock

    step "2/13 verify release directory"
    [[ -d "${P[stack_dir]}" ]]     || die "stack directory missing: ${P[stack_dir]}"
    [[ -f "${P[compose_file]}" ]]  || die "compose file missing: ${P[compose_file]}"
    [[ -f "${P[manifest_file]}" ]] || die "manifest.json missing — ship a bundle first"
    ok "release directory present"

    step "3/13 verify backup and log tree"
    dd_assert_backup_tree
    dd_open_log deploy

    step "4/13 check disk space"
    local need; need="$(dd_required_space_mib)"
    dd_assert_space "${P[stack_dir]}" "$need"
    dd_assert_space "${P[backup_root]}" "$need"

    step "5/13 reconcile versions"
    local current incoming
    current="$(dd_current_version)"
    incoming="$(dd_incoming_version)"
    info "currently deployed: ${current:-<none>}"
    info "incoming release:   $incoming"
    if [[ -n "$current" && "$current" == "$incoming" ]]; then
        if [[ "$FORCE" == true ]]; then
            warn "$incoming is already deployed — proceeding because --force was given"
        else
            die "$incoming is already deployed (use --force to redeploy)"
        fi
    fi

    step "6/13 verify checksums"
    dd_verify_checksums

    step "7/13 verify docker and environment"
    dd_assert_docker
    dd_assert_env
    DD_VERSION_FOR_COMPOSE="$incoming"
    dd_validate_compose

    # Before anything changes: a restart during the session loses ticks for good.
    step "8/13 capture guard"
    dd_guard_capture "$FORCE"

    if [[ "${DD_REQUIRE_CONFIRM:-false}" == true && "$ASSUME_YES" != true ]]; then
        if [[ -t 0 ]]; then
            local reply
            printf '\n%s  ➜ Deploy %s to %s? [y/N] %s' \
                "$_c_bold" "$incoming" "${P[environment]}" "$_c_rst"
            read -r reply || reply=''
            [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]] \
                || { warn "aborted by operator"; return 0; }
        else
            die "this stack needs --yes when running non-interactively"
        fi
    fi

    step "9/13 archive the outgoing release"
    if [[ -n "$current" ]]; then
        dd_archive_current_images "${P[rollback_images]}/$current" "$current"
    else
        info "first deploy — nothing to preserve"
    fi

    step "10/13 load new images and check them against .env"
    dd_load_images
    dd_assert_images_present "$incoming"
    # Before the swap: a setting the new release rejects must stop the deploy
    # here, with the running release untouched, not after a restart.
    dd_check_config

    step "11/13 drain capture writers and start the stack"
    trap dd_release_lease EXIT
    dd_drain_writers "$FORCE"
    # --remove-orphans is scoped to this compose project, so it removes only a
    # service this stack renamed between releases, never another stack's
    # container on this shared box.
    local -a up=(up -d --no-build --remove-orphans)
    [[ "$FORCE" == true ]] && up+=(--force-recreate)
    compose "${up[@]}" || dd_deploy_fail "$current" "$incoming" "compose up failed"
    compose ps

    step "12/13 health gate"
    local healthy=true
    if [[ "$SKIP_CHECKS" == true ]]; then
        warn "health gate SKIPPED by flag — this version is recorded unverified"
    else
        dd_health_gate || healthy=false
    fi
    if [[ "$healthy" != true ]]; then
        dd_capture_app_log "failed-$incoming"
        dd_deploy_fail "$current" "$incoming" "health gate failed"
    fi
    dd_release_lease

    dd_write_version "$incoming" "$current" active

    step "13/13 retention"
    # Last, deliberately: a failure above must never destroy a rollback target.
    dd_prune_rollbacks "${P[rollback_images]}" "${P[keep_releases]}"
    # shellcheck disable=SC2046
    dd_prune_old_images "$incoming" $(dd_rollback_versions)

    dd_summary "Deployment complete" \
        "stack=${P[stack]}" \
        "environment=${P[environment]}" \
        "version=$incoming" \
        "previous=${current:-<none>}" \
        "project=${P[compose_project]}" \
        "log=${DD_LOG_FILE:-<none>}"
}

# dd_reload_main <assume_yes> <force> — recreate the RUNNING release's
# containers so an edited .env takes effect. No images change and no version is
# written. It restarts the backend, so the capture guard applies here too.
dd_reload_main() {
    local assume_yes="$1" force="$2" current
    printf '\n%s═══ data downloader reload · %s ═══%s\n' "$_c_bold" "${P[stack]}" "$_c_rst"
    dd_lock
    dd_open_log deploy
    dd_assert_docker
    dd_assert_env
    current="$(dd_current_version)"
    [[ -n "$current" ]] || die "nothing is deployed yet; there is nothing to reload"
    DD_VERSION_FOR_COMPOSE="$current"
    dd_validate_compose
    dd_check_config
    dd_guard_capture "$force"
    if [[ "$assume_yes" != true ]]; then
        [[ -t 0 ]] || die "reload needs --yes when running non-interactively"
        local reply
        printf '\n%s  ➜ Recreate the %s containers with the current .env? [y/N] %s' \
            "$_c_bold" "$current" "$_c_rst"
        read -r reply || reply=''
        [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]] || { warn "aborted by operator"; return 0; }
    fi
    trap dd_release_lease EXIT
    dd_drain_writers "$force"
    step "recreating containers for $current"
    compose up -d --no-build --force-recreate --remove-orphans || die "compose up failed"
    compose ps
    step "health gate"
    dd_health_gate || { dd_capture_app_log "reload-failed-$current"; die "reloaded, but the stack is not healthy — check .env"; }
    dd_release_lease
    dd_summary "Reload complete" "version=$current" "log=${DD_LOG_FILE:-<none>}"
}

dd_deploy_usage() {
    cat <<'USAGE'
Deploy the staged release in this directory.

  --yes, -y       skip the confirmation prompt
  --skip-checks   start the stack but do not gate on health
  --force         redeploy even if this version is already active, and bypass
                  the capture-window and capture-running guards (a stuck stack
                  that must be replaced now); writers are still drained if the
                  backend answers, and the health gate and auto-rollback apply
  --reload        recreate the running release's containers with the current .env
  --help, -h      this message

manifest.json, paths.json, checksums.sha256, the compose file and
images/*.tar.gz must already be present; status.sh on the operator machine
places them here.
USAGE
}

# dd_deploy_fail <previous> <attempted> <reason>
#
# Marks the release failed and attempts an IMAGE-LEVEL rollback to the release
# that was running. Captured data, the archive and .env are never touched.
dd_deploy_fail() {
    local previous="$1" attempted="$2" reason="$3"
    warn "deployment failed: $reason"
    compose logs --tail 40 2>/dev/null || true

    if [[ -z "$previous" ]]; then
        dd_write_version "" "" failed "$attempted"
        compose down 2>/dev/null || true
        die "deployment failed and there is no previous version to restore"
    fi

    local rb="${P[rollback_images]}/$previous"
    if [[ ! -d "$rb" ]]; then
        dd_write_version "$previous" "" failed "$attempted"
        die "deployment failed and no rollback archive exists at $rb"
    fi

    step "AUTO-ROLLBACK to $previous"
    dd_rollback_verify "$rb"
    if ! dd_restore_images "$rb"; then
        dd_write_version "$previous" "" failed "$attempted"
        die "the rollback archive for $previous would not load — auto-rollback aborted"
    fi
    [[ -f "$rb/${P[compose_name]}" ]] && cp "$rb/${P[compose_name]}" "${P[compose_file]}"

    DD_VERSION_FOR_COMPOSE="$previous"
    if compose up -d --no-build --remove-orphans >/dev/null 2>&1 && dd_health_gate; then
        dd_write_version "$previous" "$attempted" rolled-back "$attempted"
        die "deployment failed ($reason) — automatically rolled back to $previous"
    fi

    dd_write_version "$previous" "" failed "$attempted"
    die "deployment failed ($reason) AND automatic rollback did not come up healthy — manual intervention required"
}
