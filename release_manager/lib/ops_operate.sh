#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_operate.sh — operating the deployed stack from this computer: rollback,
# reload, logs, containers, the readiness diagnosis and the resource report.
#
# Same division of labour as shipping: anything that changes containers runs in
# the VPS-native script, which holds the lock. The rest is read-only.
# ─────────────────────────────────────────────────────────────────────────────

# op_rollback <paths.json> <stack> [--list | --to VERSION | --latest] [--yes] [--force]
op_rollback() {
    local paths_file="$1" stack="$2"; shift 2
    local remote_dir script
    local -a args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list)   args+=(--list); shift ;;
            --latest) shift ;;
            --to)
                [[ "${2:-}" =~ ^[A-Za-z0-9._-]+$ ]] || { err "--to needs a version label"; exit 1; }
                args+=(--to "$2"); shift 2 ;;
            --yes|-y) args+=(--yes); shift ;;
            --force)  args+=(--force); shift ;;
            *) err "unknown rollback option: $1"; exit 1 ;;
        esac
    done
    remote_dir="$(paths_get "$paths_file" .vps.stack_dir)" || exit 1
    assert_safe_remote_dir "$remote_dir" || exit 1
    script="$(stack_attr "$stack" rollback)"
    banner "ROLLBACK · $stack"
    field "remote" "${DD_SSH_ALIAS}:${remote_dir}"
    dd_ssh "test -x '$remote_dir/$script'" \
        || { err "no rollback script on the VPS — has a bundle ever been shipped?"; exit 1; }
    ship_remote_run "$remote_dir" "$script" "${args[@]}"
}

# op_reload <paths.json> <stack> [--yes] [--force] — recreate the running release's
# containers with the current on-VPS .env. Nothing is shipped and the version
# does not change; this is how an edited .env takes effect.
op_reload() {
    local paths_file="$1" stack="$2"; shift 2
    local remote_dir
    remote_dir="$(paths_get "$paths_file" .vps.stack_dir)" || exit 1
    assert_safe_remote_dir "$remote_dir" || exit 1
    banner "RELOAD · $stack"
    ship_remote_run "$remote_dir" "$(stack_attr "$stack" deploy)" --reload "$@"
}

op_logs() {
    local paths_file="$1" file="${2:-}" dir
    dir="$(paths_get "$paths_file" .backup.deploy_log)" || exit 1
    assert_safe_remote_dir "$dir" || exit 1
    step "last 8 deploy logs in $dir"
    dd_ssh "ls -1t '$dir' 2>/dev/null | head -8" || warn "none found"
    if [[ -z "$file" && "$UI_INTERACTIVE" == true ]]; then
        printf '\n%s   ➜ log filename to view (blank for the newest): %s' "$c_bold" "$c_rst"
        read -r file || file=''
    fi
    if [[ -z "$file" ]]; then
        file="$(dd_ssh "ls -1t '$dir' 2>/dev/null | head -1" || true)"
        [[ -n "$file" ]] || return 0
    fi
    [[ "$file" =~ ^[A-Za-z0-9._-]+$ ]] || { err "invalid log filename"; exit 1; }
    printf '\n'
    dd_ssh "tail -n 80 -- '$dir/$file'" || { err "cannot read that log"; exit 1; }
}

op_containers() {
    local paths_file="$1" project container
    project="$(paths_get "$paths_file" .vps.compose_project)" || exit 1
    step "containers in project $project"
    dd_ssh "docker ps -a --filter 'label=com.docker.compose.project=$project' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"
    for container in "$(paths_get "$paths_file" .vps.backend_container)" "$(paths_get "$paths_file" .vps.frontend_container)"; do
        printf '\n'
        step "last 40 lines from $container"
        dd_ssh "docker logs --tail 40 '$container' 2>&1 || echo 'not running'"
    done
}

# op_diagnose <paths.json> — the readiness check. Counts blockers and names the
# one-line fix for each, so a first deploy is a matter of working down a list.
op_diagnose() {
    local paths_file="$1" blockers=0
    banner "VPS READINESS DIAGNOSIS"
    remote_state_reset
    remote_state_fetch "$paths_file"

    section "connectivity"
    if [[ "$(rs REACHABLE)" == yes ]]; then
        ok "SSH to $DD_SSH_ALIAS as $(rs USER)"
    else
        err "SSH to $DD_SSH_ALIAS failed"; return 1
    fi
    [[ "$(rs DOCKER)" == yes ]] && ok "docker usable without sudo" || { err "docker not usable"; blockers=$((blockers+1)); }
    [[ "$(rs COMPOSE)" == yes ]] && ok "docker compose available" || { err "docker compose missing"; blockers=$((blockers+1)); }
    local c
    for c in jq rsync flock sha256sum gzip; do
        [[ "$(rs "HAS_$c")" == yes ]] || { err "$c missing on the VPS"; blockers=$((blockers+1)); }
    done

    section "folders"
    local root backup
    root="$(paths_get "$paths_file" .vps.root)"
    backup="$(dirname "$(paths_get "$paths_file" .backup.root)")"
    if [[ "$(rs ROOT_STATE)" == writable && "$(rs BACKUP_STATE)" == writable ]]; then
        ok "$root and $backup writable"
    else
        [[ "$(rs ROOT_STATE)" == writable ]] || err "$root is $(rs ROOT_STATE) ($(rs ROOT_OWNER))"
        [[ "$(rs BACKUP_STATE)" == writable ]] || err "$backup is $(rs BACKUP_STATE) ($(rs BACKUP_OWNER))"
        provision_fix_hint "$(rs USER)" "$root" "$backup"
        blockers=$((blockers+1))
    fi
    [[ "$(rs STACK_PROVISIONED)" == yes ]] && ok "stack directory provisioned" || warn "stack directory not created yet — run Provision"
    field "free (stack)"  "$(rs DISK_STACK)"
    field "free (backup)" "$(rs DISK_BACKUP)"
    field "free (docker)" "$(rs DISK_DOCKER)"
    field "memory"        "$(rs MEM)"

    section "configuration"
    if [[ "$(rs ENV)" != present ]]; then
        warn ".env not placed yet"; blockers=$((blockers+1))
    elif [[ "$(rs ENV_EMPTY)" == yes ]]; then
        warn ".env is empty"; blockers=$((blockers+1))
    elif [[ "$(rs ENV_MODE)" =~ ^[64]00$ ]]; then
        ok ".env present ($(rs ENV_OWNER) mode $(rs ENV_MODE))"
    else
        warn ".env mode $(rs ENV_MODE) is readable by others — chmod 600 it"
        blockers=$((blockers+1))
    fi

    section "layout"
    if [[ "$(rs LEGACY_ROLLBACKS)" == yes || "$(rs LEGACY_SCRIPTS)" == yes ]]; then
        [[ "$(rs LEGACY_ROLLBACKS)" == yes ]] && warn "the old rollbacks are still in $backup/ROLLBACKS"
        [[ "$(rs LEGACY_SCRIPTS)" == yes ]] && warn "the old deploy.sh / rollback.sh are still in the stack dir"
        info "do the one-time move in APP_GUIDE.md (\"Moving from the old layout\") before the next deploy"
        blockers=$((blockers+1))
    else
        ok "contract layout in place"
    fi
    if [[ "$(rs BACKEND)" == missing ]]; then
        info "no backend container (nothing deployed yet)"
    elif rs_healthy; then
        ok "backend and frontend healthy"
    else
        warn "backend: $(rs BACKEND) · frontend: $(rs FRONTEND)"
    fi

    section "release"
    if [[ -n "$(rs LIVE_VERSION)" ]]; then
        ok "deployed: $(rs LIVE_VERSION)$([[ -z "$(rs LIVE_STATUS)" ]] || printf ' (%s) at %s' "$(rs LIVE_STATUS)" "$(rs LIVE_AT)")"
    else
        info "not deployed"
    fi
    field "rollbacks" "$( [[ -n "$(rs ROLLBACKS)" ]] && printf '%s (%s)' "$(rs ROLLBACKS)" "$(rs ROLLBACKS_SIZE)" || printf 'none' )"

    printf '\n'
    if (( blockers == 0 )); then ok "no blocking issues found"; else err "$blockers blocking issue(s)"; fi
    printf '\n'
    (( blockers == 0 ))
}

# resource_report — what the VPS has left. Never fails a caller.
resource_report() {
    remote_state_reset
    remote_state_fetch "$PATHS_FILE" 2>/dev/null || true
    [[ "$(rs REACHABLE)" == yes ]] || { warn "VPS unreachable — no resource report"; return 0; }
    field "memory"        "$(rs MEM)"
    field "free (stack)"  "$(rs DISK_STACK)"
    field "free (backup)" "$(rs DISK_BACKUP)"
    field "free (docker)" "$(rs DISK_DOCKER)"
    return 0
}
