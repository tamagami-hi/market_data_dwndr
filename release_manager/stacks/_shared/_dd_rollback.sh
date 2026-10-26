#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# _dd_rollback.sh — the VPS-native rollback flow.
#
# Runs ON THE VPS. Restores a previously archived release: its images and, when
# the archive has one, the compose file that started them, as a matched pair.
# Archives from the pre-contract pipeline (two image archives and version.txt)
# restore too, under the current compose file.
#
# The release it replaces is archived first unless it already is: deploy only
# archives the release it replaces, so the running one is usually absent, and
# without this a rollback plus the next prune would lose the only way forward.
#
# .env, MARKET_DATA and the archive are never touched.
# ─────────────────────────────────────────────────────────────────────────────

dd_rollback_main() {
    local paths_file="$1"; shift

    local TARGET='' ASSUME_YES=false SKIP_CHECKS=false LIST_ONLY=false FORCE=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --to)          TARGET="${2:-}"; shift 2 ;;
            --list)        LIST_ONLY=true; shift ;;
            --yes|-y)      ASSUME_YES=true; shift ;;
            --skip-checks) SKIP_CHECKS=true; shift ;;
            --force)       FORCE=true; shift ;;
            --help|-h)     dd_rollback_usage; return 0 ;;
            *) printf 'Unknown argument: %s\n' "$1" >&2; dd_rollback_usage >&2; return 1 ;;
        esac
    done

    require_cmds jq sha256sum gzip find sort
    dd_load_paths "$paths_file"
    P[paths_file]="$paths_file"

    printf '\n%s═══ data downloader rollback · %s ═══%s\n' "$_c_bold" "${P[stack]}" "$_c_rst"

    local current available dir
    current="$(dd_current_version)"
    # Newest archive last, by when it was archived. Only complete archives are
    # offered: a half-written save would otherwise look selectable.
    available="$(find "${P[rollback_images]}" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%f\n' 2>/dev/null \
                 | sort -n | cut -f2 \
                 | while IFS= read -r dir; do
                       dd_rollback_complete "${P[rollback_images]}/$dir" && printf '%s\n' "$dir"
                   done || true)"

    if [[ "$LIST_ONLY" == true ]]; then
        printf '\n   current: %s\n\n   available rollback targets (oldest first):\n' "${current:-<none>}"
        if [[ -n "$available" ]]; then
            while IFS= read -r dir; do
                printf '     %-24s %s  %s\n' "$dir" \
                    "$(date -r "${P[rollback_images]}/$dir" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '?')" \
                    "$(du -sh "${P[rollback_images]}/$dir" 2>/dev/null | cut -f1)"
            done <<<"$available"
        else
            printf '     <none>\n'
        fi
        printf '\n'
        return 0
    fi

    [[ -n "$available" ]] || die "no rollback archives under ${P[rollback_images]}"

    if [[ -z "$TARGET" ]]; then
        TARGET="$(printf '%s\n' "$available" | grep -vx -- "${current:-}" | tail -n1 || true)"
        [[ -n "$TARGET" ]] || die "no rollback target other than the running version"
        info "no --to given; selecting $TARGET"
    fi

    [[ "$TARGET" != "$current" ]] || die "$TARGET is already the running version"
    [[ "$TARGET" =~ ^[A-Za-z0-9._-]+$ ]] || die "refusing an unsafe version label: $TARGET"

    local rb="${P[rollback_images]}/$TARGET"
    [[ -d "$rb" ]] || die "no rollback archive for $TARGET at $rb"

    dd_lock
    dd_open_log deploy
    dd_assert_docker
    dd_assert_env

    step "1/7 verify the rollback archive"
    dd_rollback_verify "$rb"

    step "2/7 capture guard"
    dd_guard_capture "$FORCE"

    step "3/7 confirm"
    if [[ "$ASSUME_YES" != true ]]; then
        if [[ -t 0 ]]; then
            local reply
            printf '\n%s  ➜ Roll %s back to %s? Both services restart; data stays as it is. [y/N] %s' \
                "$_c_bold" "${current:-<none>}" "$TARGET" "$_c_rst"
            read -r reply || reply=''
            [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]] \
                || { warn "aborted by operator"; return 0; }
        else
            die "rollback needs --yes when running non-interactively"
        fi
    fi

    step "4/7 archive the outgoing release"
    if [[ -n "$current" ]]; then
        dd_archive_current_images "${P[rollback_images]}/$current" "$current"
    else
        info "nothing recorded as running — nothing to preserve"
    fi

    step "5/7 load the archived images"
    dd_restore_images "$rb" || die "failed to load the images archived for $TARGET"

    step "6/7 restore the matching compose file and start"
    if [[ -f "$rb/${P[compose_name]}" ]]; then
        cp "$rb/${P[compose_name]}" "${P[compose_file]}"
        ok "compose file restored from the archive"
    else
        warn "archive has no compose file — keeping the current one"
    fi
    DD_VERSION_FOR_COMPOSE="$TARGET"
    dd_validate_compose
    trap dd_release_lease EXIT
    dd_drain_writers "$FORCE"
    compose up -d --no-build --remove-orphans || die "failed to start the restored release"
    compose ps

    step "7/7 health gate"
    if [[ "$SKIP_CHECKS" == true ]]; then
        warn "health gate SKIPPED by flag"
    elif ! dd_health_gate; then
        dd_capture_app_log "rollback-failed-$TARGET"
        dd_write_version "$TARGET" "$current" unhealthy
        die "rolled back to $TARGET but it did not come up healthy — manual intervention required"
    fi
    dd_release_lease

    dd_write_version "$TARGET" "$current" rolled-back

    dd_summary "Rollback complete" \
        "stack=${P[stack]}" \
        "restored=$TARGET" \
        "was=${current:-<none>}" \
        "log=${DD_LOG_FILE:-<none>}"
    info "${current:-the previous release} stays in the rollback store, so you can move forward again"
}

dd_rollback_usage() {
    cat <<'USAGE'
Restore a previously archived release.

  --to VERSION    which release to restore (default: newest archived, not current)
  --list          show the current version and the rollback targets
  --yes, -y       skip the confirmation prompt
  --force         bypass the capture-window and capture-running guards
  --skip-checks   start the release but do not gate on health
  --help, -h      this message

Images and the compose file are restored. .env and the captured data are not touched.
USAGE
}
