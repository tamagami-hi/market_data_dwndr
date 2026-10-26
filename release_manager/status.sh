#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# status.sh — the CONTROL CENTER, and the only script an operator runs.
#
# A dashboard plus an interactive menu: Repository → Provision → Export →
# Ship + Deploy. Keeping main ready, cutting a release, exporting, shipping,
# deploying, rolling back, reloading and diagnosing all live in lib/ops_*.sh
# and run from here, so there is exactly one implementation of each.
#
# Every operation runs in a subshell: an `exit` inside one ends that operation
# and returns to the menu, never the menu itself.
#
# The VPS side is separate by necessity: data_downloader_deploy.sh and
# data_downloader_rollback.sh are shipped to the VPS and own every docker
# command there. status.sh only uploads files and invokes them over SSH.
#
# Usage:
#   ./release_manager/status.sh                    the interactive control center
#   ./release_manager/status.sh --status           print the dashboard and exit
#   ./release_manager/status.sh --diagnose         readiness check; exit 1 on blockers
#   ./release_manager/status.sh --do <op> [opts]   run one operation without the menu
#
# Operations for --do (the same ones the menu runs):
#   commit [--message TEXT] [--yes]  commit every change on main
#   integrate BRANCH [--yes]         merge origin/BRANCH into main
#   sync [--yes]                     pull --rebase origin/main, then push main
#   prs [--approve N] [--yes]        list open PRs into main, or approve one
#   release patch|minor|major [--yes] [--no-push]
#                                    bump __version__, commit, tag vX.Y.Z, push both
#   provision                        create the VPS tree, stage .env.example
#   export [--skip-build] [--backend-url URL]
#                                    build both images, prove they start, stage a bundle
#   ship                             upload the newest bundle, do not deploy
#   deploy [--yes] [--force]         upload and deploy the newest bundle
#   rollback --list | --latest | --to VERSION   [--yes] [--force]
#   reload [--yes] [--force]         recreate containers with the current .env
#   logs [FILE]                      list and tail the deploy logs
#   containers                       compose ps and recent logs of both services
#   validate                         validate the path contract
#
# --force on deploy, rollback and reload bypasses the capture-window and
# capture-running guards, for a stuck stack that must be replaced now.
# Without a terminal, every operation that would prompt needs --yes.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RM_DIR="$ROOT_DIR/release_manager"
BUILD_DIR="$RM_DIR/build"

# shellcheck source=lib/ui.sh
source "$RM_DIR/lib/ui.sh"
# shellcheck source=lib/version.sh
source "$RM_DIR/lib/version.sh"
# shellcheck source=lib/stacks.sh
source "$RM_DIR/lib/stacks.sh"
# shellcheck source=lib/hosts.sh
source "$RM_DIR/lib/hosts.sh"
# shellcheck source=lib/paths.sh
source "$RM_DIR/lib/paths.sh"
# shellcheck source=lib/remote_state.sh
source "$RM_DIR/lib/remote_state.sh"
# shellcheck source=lib/ops_repo.sh
source "$RM_DIR/lib/ops_repo.sh"
# shellcheck source=lib/ops_provision.sh
source "$RM_DIR/lib/ops_provision.sh"
# shellcheck source=lib/ops_export.sh
source "$RM_DIR/lib/ops_export.sh"
# shellcheck source=lib/ops_release.sh
source "$RM_DIR/lib/ops_release.sh"
# shellcheck source=lib/ops_ship.sh
source "$RM_DIR/lib/ops_ship.sh"
# shellcheck source=lib/ops_operate.sh
source "$RM_DIR/lib/ops_operate.sh"

# One stack and one host today. Both are still resolved through their
# registries so the guards in hosts.sh (no default host) stay in force when a
# second target is added: then this becomes a picker.
STACK=data_downloader
HOST="${DD_TARGET_HOST:-beonedge}"

for c in jq git ssh; do
    command -v "$c" >/dev/null || { err "$c is required"; exit 1; }
done

host_require "$HOST" || exit 1
PATHS_FILE="$(stack_paths_file "$STACK")" || exit 1
paths_validate "$STACK" "$PATHS_FILE" \
    || { err "the path contract failed validation — fix stacks/$STACK/paths.json"; exit 1; }

# ── helpers ─────────────────────────────────────────────────────────────────

run_op() { ( "$@" ); }

newest_bundle() { bundle_path_newest "$BUILD_DIR/$(host_build_dir "$STACK")"; }

bundle_version() {
    local b; b="$(newest_bundle)"
    [[ -n "$b" && -f "$b/manifest.json" ]] || return 0
    jq -r '.version // empty' "$b/manifest.json" 2>/dev/null || true
}

next_label() {
    local label
    label="$(build_label "$ROOT_DIR" 2>&1)" || { printf '%s\n' "$label"; return 0; }
    printf '%s (%s)\n' "${label% *}" "${label##* }"
}

rollbacks_line() {
    local list; list="$(rs ROLLBACKS)"
    if [[ -n "$list" ]]; then printf '%s (%s)\n' "${list//,/, }" "$(rs ROLLBACKS_SIZE)"; else printf 'none\n'; fi
}

# ── dashboard ───────────────────────────────────────────────────────────────

show_repository() {
    section "━━ repository"
    repo_eval
    local branch dirty=clean ahead rb n
    branch="$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD 2>/dev/null || echo detached)"
    git_dirty "$ROOT_DIR" && dirty="${c_ylw}${RS_DIRTY} uncommitted${c_rst}"
    printf '     %-14s %s%s\n' "branch" "$branch" \
        "$([[ "$branch" == main ]] || printf '  %s(releases come from main)%s' "$c_ylw" "$c_rst")"
    printf '     %-14s %s (%b)\n' "commit" "$(git -C "$ROOT_DIR" rev-parse --short=9 HEAD 2>/dev/null || echo '?')" "$dirty"
    [[ "$RS_FETCHED" == true ]] || warn "could not fetch origin — the comparison may be stale"
    if [[ "$RS_HAS_REMOTE" != true ]]; then
        warn "origin/main not found"
    elif (( RS_AHEAD == 0 && RS_BEHIND == 0 )); then
        ok "main is level with origin/main"
    else
        (( RS_AHEAD > 0 ))  && warn "main is $RS_AHEAD commit(s) ahead of origin/main — Repository → Sync"
        (( RS_BEHIND > 0 )) && warn "main is $RS_BEHIND commit(s) behind origin/main — Repository → Sync"
    fi
    ahead="$(repo_branches_ahead)"
    if [[ -n "$ahead" ]]; then
        while IFS=$'\t' read -r rb n; do
            info "origin/$rb has $n commit(s) main lacks — Repository → Integrate"
        done <<<"$ahead"
    fi
    field "next label"   "$(next_label)"
    field "last release" "$(release_label "$(latest_release_tag "$ROOT_DIR")")"
}

show_status() {
    remote_state_fetch "$PATHS_FILE"

    printf '\n%s╔════════════════════════════════════════════════════════════════╗%s\n' "$c_bold" "$c_rst"
    printf '%s║  DATA_DOWNLOADER · release control center                      ║%s\n' "$c_bold" "$c_rst"
    printf '%s╚════════════════════════════════════════════════════════════════╝%s\n' "$c_bold" "$c_rst"

    show_repository

    section "━━ vps $(host_describe)"
    if [[ "$(rs REACHABLE)" != yes ]]; then
        err "unreachable over SSH"
        printf '\n'
        return 0
    fi
    ok "reachable as $(rs USER)"
    [[ "$(rs DOCKER)" == yes ]] && ok "docker usable" || err "docker NOT usable"
    if [[ "$(rs ROOT_STATE)" == writable && "$(rs BACKUP_STATE)" == writable ]]; then
        ok "deploy and backup roots writable"
    else
        err "a root is not writable (root: $(rs ROOT_STATE), backup: $(rs BACKUP_STATE)) — Provision shows the fix"
    fi
    case "$(rs ENV)" in
        present) ok ".env present (mode $(rs ENV_MODE))" ;;
        *)       warn ".env not placed yet" ;;
    esac
    if [[ "$(rs LEGACY_ROLLBACKS)" == yes || "$(rs LEGACY_SCRIPTS)" == yes ]]; then
        warn "the one-time move to the contract layout is pending — see APP_GUIDE.md"
    fi
    field "free disk" "stack $(rs DISK_STACK) · backup $(rs DISK_BACKUP) · docker $(rs DISK_DOCKER)"

    section "━━ release"
    local built live
    built="$(bundle_version)"; live="$(rs LIVE_VERSION)"
    field "staged"   "${built:-—}"
    local live_status; live_status="$(rs LIVE_STATUS)"
    field "deployed" "${live:-—}${live_status:+ ($live_status)}"
    if [[ -n "$built" && "$built" == "$live" ]]; then
        ok "in sync"
    elif [[ -n "$built" ]]; then
        warn "ship pending"
    fi
    if [[ -n "$live" ]]; then
        if rs_healthy; then ok "backend and frontend healthy"
        else err "backend: $(rs BACKEND) · frontend: $(rs FRONTEND)"; fi
    fi
    field "rollbacks" "$(rollbacks_line)"

    local containers
    containers="$(rs_containers)"
    section "━━ containers"
    if [[ -n "$containers" ]]; then
        while IFS=$'\t' read -r name st; do printf '     %-28s %s\n' "$name" "$st"; done <<<"$containers"
    else
        info "none"
    fi
    printf '\n'
}

# ── menus ───────────────────────────────────────────────────────────────────

pause_after() {
    printf '\n%s   ➜ press Enter to continue %s' "$c_dim" "$c_rst"
    read -r _ || true
}

choose() {
    local prompt="$1" reply
    printf '%s   ➜ %s: %s' "$c_bold" "$prompt" "$c_rst" >&2
    read -r reply || reply=q
    printf '%s\n' "$reply"
}

menu_repository() {
    local b n
    while true; do
        cat <<MENU

${c_bold}━━ Repository${c_rst}   releases are cut from main; origin/main is what ships
   1) Commit                  commit every change on main
   2) Integrate a branch      merge a remote branch that is ahead of main
   3) Sync main               pull --rebase origin/main, then push main
   4) Pull requests           list open PRs into main, approve one
   b) Back
MENU
        case "$(choose 'Repository choice')" in
            1) run_op op_repo_commit || true ;;
            2) repo_eval
               while IFS=$'\t' read -r b n; do
                   [[ -n "$b" ]] && printf '     origin/%-24s %s commit(s) ahead\n' "$b" "$n"
               done < <(repo_branches_ahead)
               b="$(choose 'branch to merge (blank to cancel)')"
               [[ -z "$b" ]] || run_op op_repo_integrate "$b" || true ;;
            3) run_op op_repo_sync || true ;;
            4) run_op op_repo_prs || true
               n="$(choose 'PR number to approve (blank to skip)')"
               [[ -z "$n" ]] || run_op op_repo_prs --approve "$n" || true ;;
            b|B) return 0 ;;
            q|Q) exit 0 ;;
            *) warn "unknown choice"; continue ;;
        esac
        pause_after
    done
}

menu_provision() {
    while true; do
        cat <<MENU

${c_bold}━━ Provision${c_rst}
   1) Diagnose the VPS        readiness checks, blockers and their fixes
   2) Provision the VPS       create the folder tree, stage .env.example
   3) Validate the contract   check stacks/$STACK/paths.json
   b) Back
MENU
        case "$(choose 'Provision choice')" in
            1) run_op op_diagnose "$PATHS_FILE" || true ;;
            2) run_op op_provision "$PATHS_FILE" "$STACK" || warn "provisioning did not complete" ;;
            3) paths_validate "$STACK" "$PATHS_FILE" && ok "paths.json validates" ;;
            b|B) return 0 ;;
            q|Q) exit 0 ;;
            *) warn "unknown choice"; continue ;;
        esac
        pause_after
    done
}

menu_export() {
    while true; do
        cat <<MENU

${c_bold}━━ Export${c_rst}
   1) Build bundle            build both images, prove they start, stage a bundle
   2) Re-stage bundle         reuse the images already built for this version
   3) Cut a release           bump the version, commit, tag and push (then Build)
   b) Back
MENU
        case "$(choose 'Export choice')" in
            1) run_op op_export "$PATHS_FILE" "$STACK" || warn "export did not complete" ;;
            2) run_op op_export "$PATHS_FILE" "$STACK" --skip-build || warn "re-stage did not complete" ;;
            3) menu_release || true ;;
            b|B) return 0 ;;
            q|Q) exit 0 ;;
            *) warn "unknown choice"; continue ;;
        esac
        pause_after
    done
}

# Each part with the version it would cut, read from __version__ now.
menu_release() {
    local current latest
    current="$(pkg_version "$ROOT_DIR")" || return 1
    assert_semver "$current" || return 1
    latest="$(latest_release_tag "$ROOT_DIR")"
    cat <<MENU

${c_bold}━━ Cut a release${c_rst}   __version__ $current · last release $(release_label "$latest")
   1) Patch   $current → $(bump_version "$current" patch)   fixes only
   2) Minor   $current → $(bump_version "$current" minor)   new features, nothing breaks
   3) Major   $current → $(bump_version "$current" major)   breaking changes
   b) Back
MENU
    case "$(choose 'Release choice')" in
        1) run_op op_release patch || warn "release did not complete" ;;
        2) run_op op_release minor || warn "release did not complete" ;;
        3) run_op op_release major || warn "release did not complete" ;;
        b|B) return 0 ;;
        q|Q) exit 0 ;;
        *) warn "unknown choice" ;;
    esac
}

menu_ship() {
    local v
    while true; do
        cat <<MENU

${c_bold}━━ Ship + Deploy${c_rst}
   1) Ship + deploy           upload the newest bundle and deploy it
   2) Ship only               upload for inspection; do not deploy
   3) Force redeploy          same version, and bypass the capture guards
   4) Reload                  recreate containers with the current .env
   5) Roll back               to the newest archived release
   6) Roll back to …          pick a version from the list
   7) View deploy logs        tail a deploy log
   8) Inspect containers      compose ps and recent logs
   b) Back
MENU
        case "$(choose 'Ship + Deploy choice')" in
            1) run_op op_ship "$PATHS_FILE" "$STACK" deploy || warn "deploy did not complete" ;;
            2) run_op op_ship "$PATHS_FILE" "$STACK" ship-only || warn "shipping did not complete" ;;
            3) run_op op_ship "$PATHS_FILE" "$STACK" force || warn "redeploy did not complete" ;;
            4) run_op op_reload "$PATHS_FILE" "$STACK" || warn "reload did not complete" ;;
            5) run_op op_rollback "$PATHS_FILE" "$STACK" --latest || warn "rollback did not complete" ;;
            6) run_op op_rollback "$PATHS_FILE" "$STACK" --list || true
               v="$(choose 'version to restore (blank to cancel)')"
               [[ -z "$v" ]] || run_op op_rollback "$PATHS_FILE" "$STACK" --to "$v" || warn "rollback did not complete" ;;
            7) run_op op_logs "$PATHS_FILE" || true ;;
            8) run_op op_containers "$PATHS_FILE" || true ;;
            b|B) return 0 ;;
            q|Q) exit 0 ;;
            *) warn "unknown choice"; continue ;;
        esac
        remote_state_reset
        pause_after
    done
}

menu_main() {
    while true; do
        show_status
        cat <<MENU
${c_bold}━━ workflows${c_rst}
   1) Repository              commit, integrate, sync main, pull requests
   2) Provision               diagnose the VPS and prepare its folders
   3) Export                  cut a release, build and stage a bundle
   4) Ship + Deploy           deploy, reload, roll back, logs
   r) Refresh
   q) Quit

MENU
        case "$(choose 'workflow')" in
            1) menu_repository ;;
            2) menu_provision ;;
            3) menu_export ;;
            4) menu_ship ;;
            r|R) remote_state_reset ;;
            q|Q) printf '\n'; return 0 ;;
            *) warn "unknown workflow" ;;
        esac
        remote_state_reset
    done
}

# ── entry ───────────────────────────────────────────────────────────────────

do_op() {
    local op="${1:-}"; shift || true
    case "$op" in
        commit)     run_op op_repo_commit "$@" ;;
        integrate)  run_op op_repo_integrate "$@" ;;
        sync)       run_op op_repo_sync "$@" ;;
        prs)        run_op op_repo_prs "$@" ;;
        release)    run_op op_release "$@" ;;
        provision)  run_op op_provision "$PATHS_FILE" "$STACK" ;;
        export)     run_op op_export "$PATHS_FILE" "$STACK" "$@" ;;
        ship)       run_op op_ship "$PATHS_FILE" "$STACK" ship-only "$@" ;;
        deploy)
            local mode=deploy a
            local -a rest=()
            for a in "$@"; do
                if [[ "$a" == --force ]]; then mode=force; else rest+=("$a"); fi
            done
            run_op op_ship "$PATHS_FILE" "$STACK" "$mode" "${rest[@]}" ;;
        rollback)   run_op op_rollback "$PATHS_FILE" "$STACK" "$@" ;;
        reload)     run_op op_reload "$PATHS_FILE" "$STACK" "$@" ;;
        logs)       run_op op_logs "$PATHS_FILE" "$@" ;;
        containers) run_op op_containers "$PATHS_FILE" ;;
        validate)   paths_validate "$STACK" "$PATHS_FILE" && ok "paths.json validates" ;;
        diagnose)   run_op op_diagnose "$PATHS_FILE" ;;
        *) err "unknown operation: ${op:-<none>} (see --help)"; return 1 ;;
    esac
}

status_main() {
    case "${1:-}" in
        --status)   show_status ;;
        --diagnose) run_op op_diagnose "$PATHS_FILE" ;;
        --do)       shift; do_op "$@" ;;
        --help|-h)  sed -n '2,/^# ───.*$/{/^# ───/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}" | sed -n '1,50p' ;;
        "")
            [[ "$UI_INTERACTIVE" == true ]] \
                || { err "not a terminal — use --status, --diagnose or --do <op>"; return 1; }
            menu_main ;;
        *) err "unknown argument: $1 (see --help)"; return 1 ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    status_main "$@"
fi
