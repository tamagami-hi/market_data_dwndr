#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# paths.sh — the path contract reader.
#
# stacks/<stack>/paths.json is the SOLE authority for every path a deployment
# touches. Nothing derives a remote path in code; it is read from the contract
# and validated first. A path change is an edit to that JSON file.
#
# The contract is hand-written and tracked in git. It is never generated,
# regenerated or rewritten by any script here — Export copies it byte for byte,
# and Ship refuses a bundle whose copy has drifted.
# ─────────────────────────────────────────────────────────────────────────────

PATHS_SCHEMA_REQUIRED=1
PATHS_IMAGE_PREFIX=market-data-dwndr

# stack_paths_file <stack> — the tracked contract for a stack on the selected
# host. The bundle always carries it as paths.json.
stack_paths_file() {
    local stack="$1" file name=paths.json
    if [[ -n "${DD_HOST:-}" ]] && declare -F host_attr >/dev/null; then
        name="$(host_attr "$DD_HOST" paths 2>/dev/null || printf 'paths.json')"
    fi
    file="${RM_DIR:?RM_DIR must be set}/stacks/$stack/$name"
    [[ -f "$file" ]] || { printf 'missing path contract: %s\n' "$file" >&2; return 1; }
    printf '%s\n' "$file"
}

# paths_get <file> <jq-query> — one value, failing on null or absent.
paths_get() {
    local file="$1" query="$2" value
    value="$(jq -r "$query // empty" "$file" 2>/dev/null)" || {
        printf 'cannot read %s from %s\n' "$query" "$file" >&2; return 1; }
    [[ -n "$value" ]] || { printf '%s is null or missing in %s\n' "$query" "$file" >&2; return 1; }
    printf '%s\n' "$value"
}

# paths_get_opt <file> <jq-query> — one value, empty string when absent.
paths_get_opt() {
    jq -r "${2} // empty" "$1" 2>/dev/null || true
}

_paths_absolute_under() {
    local key="$1" value="$2" parent="${3:-}"
    # Contract values are interpolated into single-quoted remote commands, so
    # a quote, a space or a $ here would be an injection on the VPS. Only the
    # plain path alphabet is allowed.
    [[ "$value" =~ ^/[A-Za-z0-9_./-]+$ ]] || { printf '%s has characters outside [A-Za-z0-9_./-]: %s\n' "$key" "$value" >&2; return 1; }
    [[ "$value" != *".."* ]] || { printf '%s must not contain ..: %s\n' "$key" "$value" >&2; return 1; }
    [[ -z "$parent" || "$value" == "$parent"/* ]] || {
        printf '%s must live under %s: %s\n' "$key" "$parent" "$value" >&2; return 1; }
}

# paths_validate <stack> <file> — prove the contract is usable before anything
# acts on it. Fails closed: a malformed contract must never reach the VPS,
# because by then a wrong path is a wrong rm -rf.
paths_validate() {
    local stack="$1" file="$2" schema declared value key

    [[ -f "$file" ]] || { printf 'no such contract: %s\n' "$file" >&2; return 1; }
    jq empty "$file" 2>/dev/null || { printf 'contract is not valid JSON: %s\n' "$file" >&2; return 1; }

    schema="$(jq -r '.schema // 0' "$file")"
    [[ "$schema" == "$PATHS_SCHEMA_REQUIRED" ]] || {
        printf 'contract schema %s, expected %s: %s\n' "$schema" "$PATHS_SCHEMA_REQUIRED" "$file" >&2
        return 1; }

    # Identity check. Binds a contract to one stack so a bundle built for
    # another stack cannot be deployed here even if every path happens to parse.
    declared="$(jq -r '.stack // empty' "$file")"
    [[ "$declared" == "$stack" ]] || {
        printf 'contract declares stack %s, expected %s\n' "${declared:-<none>}" "$stack" >&2
        return 1; }

    # The VPS builds image tags from this prefix and Export from
    # stack_image_tag. If they disagree, the deploy loads one tag and compose
    # asks for another.
    value="$(paths_get "$file" .image_prefix)" || return 1
    [[ "$value" == "$PATHS_IMAGE_PREFIX" ]] || {
        printf 'image_prefix is %s, but stacks.sh tags images %s-*\n' "$value" "$PATHS_IMAGE_PREFIX" >&2
        return 1; }

    local root stack_dir backup_root
    root="$(paths_get "$file" .vps.root)" || return 1
    stack_dir="$(paths_get "$file" .vps.stack_dir)" || return 1
    backup_root="$(paths_get "$file" .backup.root)" || return 1

    # Every path that will be created, written or removed must be absolute and
    # free of traversal. A relative path here would resolve against whatever
    # directory the remote script happened to be in.
    for key in \
        .vps.root .vps.stack_dir .vps.paths_file .vps.images_dir \
        .vps.compose_file .vps.env_file .vps.env_example .vps.version_file \
        .vps.manifest_file .vps.checksums_file .vps.deploy_script \
        .vps.rollback_script .vps.lock_file \
        .backup.root .backup.rollback_root .backup.rollback_images \
        .backup.logs_root .backup.deploy_log .backup.app_log
    do
        value="$(paths_get "$file" "$key")" || return 1
        _paths_absolute_under "$key" "$value" || return 1
    done

    # The stack dir is the root itself or sits under it, or archiving would
    # reach outside the tree this contract owns. (The downloader's stack dir IS
    # its root: it predates the contract and holds the live MARKET_DATA.)
    [[ "$stack_dir" == "$root" || "$stack_dir" == "$root"/* ]] || {
        printf 'stack_dir %s is not under root %s\n' "$stack_dir" "$root" >&2; return 1; }

    # Pruning removes subdirectories of rollback_images, so it must sit inside
    # the backup tree and never be, or contain, the stack dir.
    value="$(paths_get "$file" .backup.rollback_images)"
    _paths_absolute_under .backup.rollback_images "$value" "$backup_root" || return 1
    [[ "$stack_dir" != "$value"/* && "$stack_dir" != "$value" ]] || {
        printf 'rollback_images must not contain the stack dir\n' >&2; return 1; }

    # The live capture directory sits in the stack dir on this box; it is
    # declared so the bundle can never carry a file of that name.
    value="$(paths_get_opt "$file" .vps.market_data_dir)"
    [[ -z "$value" ]] || _paths_absolute_under .vps.market_data_dir "$value" || return 1

    for key in .vps.compose_name .vps.version_name .vps.backend_container \
               .vps.frontend_container .vps.compose_project .vps.docker \
               .environment .short
    do
        value="$(paths_get "$file" "$key")" || return 1
        [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || {
            printf '%s must be a plain name [A-Za-z0-9_.-]: %s\n' "$key" "$value" >&2; return 1; }
    done

    # At least one image, each with a key and an archive filename. The archive
    # is a bare filename: it is joined onto images_dir and onto the rollback
    # directory, so a path separator here would escape both.
    local count
    count="$(jq -r '.images | length' "$file" 2>/dev/null || echo 0)"
    (( count >= 1 )) || { printf 'contract declares no images\n' >&2; return 1; }
    while IFS=$'\t' read -r key value; do
        [[ "$key" =~ ^[a-z0-9_-]+$ ]] || { printf 'image key must be [a-z0-9_-]: %s\n' "$key" >&2; return 1; }
        [[ -n "$value" ]]      || { printf 'image %s has no archive\n' "$key" >&2; return 1; }
        [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || { printf 'image archive must be a bare filename: %s\n' "$value" >&2; return 1; }
    done < <(jq -r '.images[] | [.key, .archive] | @tsv' "$file")

    [[ "$(paths_get "$file" .health.mode)" == docker ]] || {
        printf 'health.mode must be docker (the containers'"'"' own healthchecks)\n' >&2; return 1; }
    value="$(paths_get "$file" .health.settle_seconds)" || return 1
    [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 30 )) || {
        printf 'health.settle_seconds must be an integer of at least 30\n' >&2; return 1; }

    for key in .capture.default_open .capture.default_close; do
        value="$(paths_get "$file" "$key")" || return 1
        [[ "$value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
            printf '%s must be HH:MM: %s\n' "$key" "$value" >&2; return 1; }
    done
    value="$(paths_get "$file" .capture.default_timezone)" || return 1
    [[ "$value" =~ ^[A-Za-z_]+(/[A-Za-z_+-]+)*$ ]] || {
        printf 'capture.default_timezone is not a zone name: %s\n' "$value" >&2; return 1; }

    local keep
    keep="$(paths_get "$file" .retention.keep_releases)" || return 1
    [[ "$keep" =~ ^[0-9]+$ ]] && (( keep >= 1 )) || {
        printf 'retention.keep_releases must be a positive integer\n' >&2; return 1; }
}
