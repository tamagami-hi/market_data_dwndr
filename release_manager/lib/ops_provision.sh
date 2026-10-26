#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_provision.sh — create the directory tree this pipeline expects on the VPS.
#
# Run from status.sh (Provision menu, or --do provision). Every op_* function
# runs in a subshell, so `exit` ends the operation and returns to the menu.
#
# Idempotent: it creates directories and stages .env.example, and it never
# touches an existing .env or the live MARKET_DATA in the stack directory.
#
# It never runs sudo. /srv/backup is root's, so if either root is missing or
# not writable by the deploy user this prints the exact one-line fix and stops.
# On this box both already exist and belong to the deploy user. Everything under them is then created as the deploy user, the same
# way every sibling stack on this box is laid out.
# ─────────────────────────────────────────────────────────────────────────────

op_provision() {
    local paths_file="$1" stack="$2"
    local root stack_dir backup_project deploy_user dir line
    root="$(paths_get "$paths_file" .vps.root)" || exit 1
    stack_dir="$(paths_get "$paths_file" .vps.stack_dir)" || exit 1
    backup_project="$(dirname "$(paths_get "$paths_file" .backup.root)")"
    for dir in "$root" "$stack_dir" "$backup_project"; do
        assert_safe_remote_dir "$dir" || exit 1
    done

    banner "PROVISION · $stack"
    field "host"   "$(host_describe)"
    field "root"   "$root"
    field "stack"  "$stack_dir"
    field "backup" "$backup_project"

    step "checking SSH connectivity"
    dd_ssh true 2>/dev/null || { err "cannot reach $DD_SSH_ALIAS over SSH"; exit 1; }
    ok "SSH ok"

    # Reported, never fixed: changing ownership needs sudo, and sudo on this
    # box asks for a password, which is the operator's alone.
    step "checking both roots are writable by the deploy user"
    local state
    state="$(dd_ssh "bash -s -- '$root' '$backup_project'" <<'REMOTE' || true
set -u
for dir in "$@"; do
    if [[ ! -e "$dir" ]]; then
        printf 'missing %s\n' "$dir"
    elif [[ -w "$dir" ]]; then
        printf 'ok %s\n' "$dir"
    else
        printf 'readonly %s (%s)\n' "$dir" "$(stat -c '%U:%G %a' "$dir")"
    fi
done
printf 'user %s\n' "$(id -un)"
REMOTE
)"
    deploy_user="$(sed -n 's/^user //p' <<<"$state")"
    if grep -qE '^(missing|readonly) ' <<<"$state"; then
        while IFS= read -r line; do
            [[ "$line" == missing* || "$line" == readonly* ]] && warn "$line"
        done <<<"$state"
        err "the deploy user ${deploy_user:-?} cannot write to the root(s) above"
        provision_fix_hint "${deploy_user:-beonedge}" "$root" "$backup_project"
        exit 1
    fi
    ok "both roots writable by $deploy_user"

    # Every directory comes from the contract, so this cannot drift from what
    # the deploy scripts assert.
    local -a dirs
    mapfile -t dirs < <(jq -r '
        [ .vps.stack_dir, .vps.images_dir,
          .backup.root, .backup.rollback_root, .backup.rollback_images,
          .backup.logs_root, .backup.deploy_log, .backup.app_log,
          ] | .[]' "$paths_file")
    (( ${#dirs[@]} > 0 )) || { err "the contract declares no directories"; exit 1; }
    # Validated locally before they become part of a remote command line, so a
    # hostile or truncated contract value cannot reach mkdir on the VPS.
    for dir in "${dirs[@]}"; do
        assert_safe_remote_dir "$dir" || exit 1
    done

    step "creating the directory tree"
    # Arguments, not stdin: the heredoc already owns ssh's stdin.
    dd_ssh "bash -s -- $(printf "'%s' " "${dirs[@]}")" <<'REMOTE' || { err "failed to create the tree"; exit 1; }
set -euo pipefail
for dir in "$@"; do
    [[ -n "$dir" && "$dir" == /* ]] || { printf 'refusing path: %s\n' "$dir" >&2; exit 1; }
    # An existing directory keeps its mode: the stack dir predates this
    # pipeline and holds the live MARKET_DATA.
    [[ -d "$dir" ]] && { printf '  %s (exists)\n' "$dir"; continue; }
    mkdir -p -- "$dir"
    # Captured container logs may hold account names and broker errors; they
    # are not for other users on a shared box.
    case "$dir" in
        */logs|*/logs/*) chmod 750 -- "$dir" ;;
        *)               chmod 755 -- "$dir" ;;
    esac
    printf '  %s\n' "$dir"
done
REMOTE
    ok "directory tree present"

    step "staging .env.example"
    dd_ssh_opts || exit 1
    local rsync_ssh
    printf -v rsync_ssh '%q ' ssh "${DD_SSH_OPTS[@]}"
    rsync -az --chmod=F644 -e "$rsync_ssh" \
        "$RM_DIR/stacks/$stack/.env.example" "${DD_SSH_ALIAS}:${stack_dir}/" \
        || { err "failed to stage .env.example"; exit 1; }
    ok ".env.example staged"

    step "checking docker on the VPS"
    dd_ssh 'docker info >/dev/null 2>&1' || { err "docker is not usable by the deploy user"; exit 1; }
    dd_ssh 'docker compose version >/dev/null 2>&1' || { err "docker compose plugin missing"; exit 1; }
    ok "docker and compose usable without sudo"

    local env_state
    env_state="$(dd_ssh "test -e '$stack_dir/.env' && echo present || echo missing")"
    banner "PROVISIONED"
    if [[ "$env_state" == present ]]; then
        ok ".env is present — left completely untouched"
    else
        warn ".env is not present; a deploy will refuse to run without it"
        printf '\n   Place it yourself at %s:%s/.env with mode 600.\n' "$DD_SSH_ALIAS" "$stack_dir"
        printf '   .env.example beside it lists every variable. No script here reads,\n'
        printf '   writes or removes that file.\n\n'
    fi
}

# provision_fix_hint <user> <dir...> — the one command that unblocks provisioning.
# install -d creates a missing directory and sets the owner and mode of an
# existing one without touching anything inside it.
provision_fix_hint() {
    local user="$1"; shift
    printf '\n   %sRun once on the VPS (it asks for your sudo password):%s\n\n' "$c_bold" "$c_rst"
    printf '     ssh -t %s sudo install -d -o %s -g %s -m 750 %s\n\n' \
        "$DD_SSH_ALIAS" "$user" "$user" "$*"
}
