#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stacks.sh — the stack registry.
#
# AUTHORITY BOUNDARY
# This file owns stack IDENTITY only: ids, image naming, and non-path metadata
# (compose filename, script names, the compose project). It owns NO paths. Every
# deployment, backup, log and image path lives in the stack's tracked
# paths.json contract and is read through lib/paths.sh.
#
# One deployable per repository, so there is exactly one stack. The registry
# shape is kept so a second stack (a staging copy, say) is an edit to these
# tables.
# ─────────────────────────────────────────────────────────────────────────────

# Set by host_use in lib/hosts.sh, or by an operator targeting a box that is not
# in the host table. Empty fails closed in dd_ssh_opts.
DD_SSH_ALIAS="${DD_SSH_ALIAS:-}"

DD_STACKS=(data_downloader)

# stack_attr <stack> <attr> — echo one non-path attribute, or return 1.
#
#   env            environment name
#   compose        compose filename inside the stack dir
#   version_file   per-stack version filename
#   deploy         native deploy script filename
#   rollback       native rollback script filename
#   guide          guide filename
#   project        compose project name
#   backend        the backend container name
#   frontend       the frontend container name
stack_attr() {
    local stack="$1" attr="$2"
    case "$stack" in
        data_downloader)
            case "$attr" in
                env)          printf 'production\n' ;;
                compose)      printf 'docker-compose.yml\n' ;;
                version_file) printf 'version.json\n' ;;
                deploy)       printf 'data_downloader_deploy.sh\n' ;;
                rollback)     printf 'data_downloader_rollback.sh\n' ;;
                guide)        printf 'APP_GUIDE.md\n' ;;
                project)      printf 'data-downloader\n' ;;
                backend)      printf 'market-data-dwndr-backend\n' ;;
                frontend)     printf 'market-data-dwndr-frontend\n' ;;
                *) return 1 ;;
            esac ;;
        *) return 1 ;;
    esac
}

is_stack() {
    local s
    for s in "${DD_STACKS[@]}"; do [[ "$s" == "$1" ]] && return 0; done
    return 1
}

# stack_images <stack> — one "key:archive" pair per line. Both images are built
# from this repository and versioned together.
stack_images() {
    case "$1" in
        data_downloader) printf 'backend:backend.tar.gz\nfrontend:frontend.tar.gz\n' ;;
        *) return 1 ;;
    esac
}

# stack_image_tag <stack> <key> <version> — the fully qualified local image tag.
# The VPS-side library builds the same name from paths.json (.image_prefix), and
# the root compose.yaml tags its builds the same way, so all three must agree;
# paths_validate checks the prefix.
stack_image_tag() {
    local stack="$1" key="$2" version="$3"
    is_stack "$stack" || return 1
    printf 'market-data-dwndr-%s:%s\n' "$key" "$version"
}

# ── ssh plumbing ────────────────────────────────────────────────────────────
# DD_SSH_ALIAS and DD_SSH_KEY are operator-controlled and become ssh argv, so
# they are charset-validated first: an alias like "-oProxyCommand=..." would
# otherwise smuggle an extra option.
dd_ssh_opts() {
    if [[ -z "$DD_SSH_ALIAS" ]]; then
        printf 'dd_ssh_opts: no host selected; pass a host selector or set DD_SSH_ALIAS\n' >&2
        return 1
    fi
    if [[ ! "$DD_SSH_ALIAS" =~ ^[A-Za-z0-9._-]+$ || "$DD_SSH_ALIAS" == -* ]]; then
        printf 'dd_ssh_opts: unsafe DD_SSH_ALIAS: %s\n' \
            "$(printf '%s' "$DD_SSH_ALIAS" | LC_ALL=C tr -d '\000-\010\013-\037\177')" >&2
        return 1
    fi
    DD_SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)
    if [[ -n "${DD_SSH_KEY:-}" ]]; then
        if [[ ! "$DD_SSH_KEY" =~ ^[A-Za-z0-9._/~+-]+$ || "$DD_SSH_KEY" == -* ]]; then
            printf 'dd_ssh_opts: unsafe DD_SSH_KEY path\n' >&2
            return 1
        fi
        DD_SSH_OPTS+=(-i "$DD_SSH_KEY" -o IdentitiesOnly=yes)
    fi
}

dd_ssh() {
    dd_ssh_opts || return 1
    ssh "${DD_SSH_OPTS[@]}" "$DD_SSH_ALIAS" "$@"
}

# assert_safe_remote_dir <path> — refuse a path that could be the filesystem
# root or escape by traversal, so a later rm -rf inside it cannot become
# catastrophic. Depth >= 3 means a truncated variable cannot resolve to a
# top-level directory.
assert_safe_remote_dir() {
    local dir="${1:-}" shown depth
    shown="$(printf '%s' "$dir" | LC_ALL=C tr -d '\000-\010\013-\037\177')"
    [[ -n "$dir" ]]                     || { printf 'Refusing empty remote dir\n' >&2; return 1; }
    [[ "$dir" != *".."* ]]              || { printf 'Refusing remote dir with ..: %s\n' "$shown" >&2; return 1; }
    [[ "$dir" =~ ^/[A-Za-z0-9_./-]+$ ]] || { printf 'Refusing unsafe remote dir: %s\n' "$shown" >&2; return 1; }
    depth="$(printf '%s' "${dir//[!\/]/}" | wc -c)"
    (( depth >= 3 )) || { printf 'Refusing shallow remote dir: %s\n' "$shown" >&2; return 1; }
}
