#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# hosts.sh — the host registry.
#
# AUTHORITY BOUNDARY
# This file owns HOST identity only: ids, the ssh alias each id maps to, and
# descriptive metadata. It owns NO paths and NO stack identity. A stack says
# WHAT is deployed; a host says WHERE.
#
# THERE IS DELIBERATELY NO DEFAULT HOST
# Only one box exists today, the home VPS. The selector is required anyway: the
# box also runs unrelated production services (boe-*, cbl-*, aie-*), and a second
# target added later must not inherit a silent default. Adding a box is an edit
# to the table below and to ~/.ssh/config, nowhere else.
#
# The ssh alias is the only value here that becomes ssh argv. It is resolved
# from this table rather than from user input, and dd_ssh_opts charset-validates
# it again before use.
# ─────────────────────────────────────────────────────────────────────────────

DD_HOSTS=(beonedge)

DD_HOST="${DD_HOST:-}"

# host_attr <host> <attr> — echo one attribute, or return 1.
#
#   alias      ssh config alias; must exist in ~/.ssh/config
#   label      the machine's hostname, used in prompts and output
#   paths      the tracked path contract this box deploys against, a file in
#              stacks/<stack>/ (a filename only: the paths stay in the contract)
#   build      this host's bundle directory under build/
host_attr() {
    local host="$1" attr="$2"
    case "$host" in
        beonedge)
            case "$attr" in
                # Reached over Tailscale (100.x). beonedge-pub is the public
                # fallback in ~/.ssh/config and is deliberately not used here:
                # deploys go over the private network only.
                alias)  printf 'beonedge\n' ;;
                label)  printf 'beonedge-vps\n' ;;
                paths)  printf 'paths.json\n' ;;
                build)  printf 'data_downloader\n' ;;
                *) return 1 ;;
            esac ;;
        *) return 1 ;;
    esac
}

is_host() {
    local h
    for h in "${DD_HOSTS[@]}"; do [[ "$h" == "$1" ]] && return 0; done
    return 1
}

host_usage() {
    local h
    printf 'Expected one of:\n' >&2
    for h in "${DD_HOSTS[@]}"; do
        printf '  %-10s %s\n' "$h" "$(host_attr "$h" label)" >&2
    done
}

# host_use <host> — bind the session to one host by setting the alias dd_ssh
# reads.
host_use() {
    local host="$1" alias
    alias="$(host_attr "$host" alias)" || {
        printf 'host_use: unknown host: %s\n' "$host" >&2
        return 1
    }
    DD_HOST="$host"
    DD_SSH_ALIAS="$alias"
    export DD_HOST DD_SSH_ALIAS
}

# host_require <host> — fail closed when no host was selected. An operator who
# sets DD_SSH_ALIAS directly is targeting a box outside this table on purpose,
# which is allowed and says so in the output.
host_require() {
    local host="${1:-}"
    if [[ -n "$host" ]]; then
        host_use "$host" || return 1
        return 0
    fi
    if [[ -n "${DD_SSH_ALIAS:-}" ]]; then
        DD_HOST='(DD_SSH_ALIAS)'
        export DD_HOST
        return 0
    fi
    printf 'A host is required: there is no default.\n' >&2
    host_usage >&2
    return 1
}

# host_describe — one line naming the box a command is about to act on, so the
# confirmation prompt never says only "the VPS".
host_describe() {
    if [[ "$DD_HOST" == '(DD_SSH_ALIAS)' ]]; then
        printf '%s (DD_SSH_ALIAS override)\n' "$DD_SSH_ALIAS"
        return 0
    fi
    printf '%s [%s]\n' "$(host_attr "$DD_HOST" label)" "$DD_SSH_ALIAS"
}

# host_build_dir <stack> — the bundle directory for the bound host, under build/.
host_build_dir() {
    local stack="${1:-}" name
    if [[ "$DD_HOST" == '(DD_SSH_ALIAS)' || -z "$DD_HOST" ]]; then
        printf '%s\n' "$stack"
        return 0
    fi
    name="$(host_attr "$DD_HOST" build)" || return 1
    printf '%s\n' "$name"
}
