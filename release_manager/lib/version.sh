#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# version.sh — version labelling.
#
# backend/app/__init__.py (__version__) is the single source of truth for the
# canonical version: the backend reports it, and a second VERSION file would
# only be one more thing to forget to bump.
#
# Labels:
#   clean tree, on the exact vX.Y.Z tag  →  X.Y.Z              (releasable)
#   anything else                        →  <next>-dev.N.gSHA[.dirty]
#
# The patch is bumped for a dev label so it always sorts after the last release.
# `status.sh --do release` moves __version__ and the git tag together
# (ops_release.sh), so the two never disagree. Export only reads the version;
# nothing but a release writes it.
# ─────────────────────────────────────────────────────────────────────────────

# pkg_version_file <root> — the file that holds __version__.
pkg_version_file() { printf '%s/backend/app/__init__.py\n' "$1"; }

# pkg_version <root> — the bare __version__.
pkg_version() {
    local file; file="$(pkg_version_file "$1")"
    [[ -f "$file" ]] || { printf 'no such file: %s\n' "$file" >&2; return 1; }
    sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$file" | head -n1
}

# set_pkg_version <root> <x.y.z> — rewrite only the __version__ line.
set_pkg_version() {
    local file updated
    file="$(pkg_version_file "$1")"
    updated="$(awk -v version="$2" '
        !done && /^__version__ = "/ { print "__version__ = \"" version "\""; done = 1; next }
        { print }
    ' "$file")" || return 1
    printf '%s\n' "$updated" > "$file"
}

assert_semver() {
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && return 0
    printf 'not a bare semver version: %s\n' "${1:-<empty>}" >&2
    return 1
}

# bump_version <x.y.z> <major|minor|patch>
bump_version() {
    local version="$1" part="${2:-patch}" major minor patch
    IFS=. read -r major minor patch <<<"$version"
    case "$part" in
        major) printf '%d.0.0\n' $((major + 1)) ;;
        minor) printf '%d.%d.0\n' "$major" $((minor + 1)) ;;
        patch) printf '%d.%d.%d\n' "$major" "$minor" $((patch + 1)) ;;
        *) printf 'unknown bump part: %s\n' "$part" >&2; return 1 ;;
    esac
}

# version_gt <a> <b> — a sorts after b as a version (both bare X.Y.Z).
version_gt() {
    [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" == "$1" ]]
}

# latest_release_tag <root> — the highest vX.Y.Z tag, without the v, or empty.
latest_release_tag() {
    git -C "$1" tag --list 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null \
        | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } \
        | sed 's/^v//' | sort -V | tail -n1
}

# release_label <x.y.z or empty> — "vX.Y.Z", or "none" before the first release.
release_label() {
    if [[ -n "$1" ]]; then printf 'v%s\n' "$1"; else printf 'none\n'; fi
}

# head_release_tags <root> — every vX.Y.Z tag on HEAD, without the v.
head_release_tags() {
    git -C "$1" tag --points-at HEAD --list 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null \
        | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sed 's/^v//'
}

git_dirty() {
    [[ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]]
}

# on_exact_release_tag <root> <version> — HEAD is exactly v<version>, clean.
on_exact_release_tag() {
    local root="$1" version="$2"
    git_dirty "$root" && return 1
    git -C "$root" describe --exact-match --tags HEAD 2>/dev/null | grep -qx "v$version"
}

# dev_version <base> <root> — <base>-dev.<commits>.g<sha>[.dirty]
dev_version() {
    local base="$1" root="$2" count sha suffix=''
    count="$(git -C "$root" rev-list --count HEAD 2>/dev/null || echo 0)"
    sha="$(git -C "$root" rev-parse --short=9 HEAD 2>/dev/null || echo unknown)"
    git_dirty "$root" && suffix='.dirty'
    printf '%s-dev.%s.g%s%s\n' "$base" "$count" "$sha" "$suffix"
}

# build_label <root> — the label Export gives a build of the current tree:
# "X.Y.Z stable" on a clean release tag, else "<next>-dev… dev". Fails when a
# release tag on HEAD names another version than __version__.
build_label() {
    local root="$1" canonical tagged
    canonical="$(pkg_version "$root")" || return 1
    assert_semver "$canonical" || return 1
    for tagged in $(head_release_tags "$root"); do
        [[ "$tagged" == "$canonical" ]] || {
            printf 'HEAD is tagged v%s but __version__ says %s; fix one with release\n' "$tagged" "$canonical" >&2
            return 1; }
    done
    if on_exact_release_tag "$root" "$canonical"; then
        printf '%s stable\n' "$canonical"
    else
        printf '%s dev\n' "$(dev_version "$(bump_version "$canonical" patch)" "$root")"
    fi
}

# bundle_dirs_oldest_first <dir> — bundle basenames by build time, oldest first.
# Sorted on the UTC stamp in the directory name rather than mtime, so a bundle
# that was merely touched does not survive pruning ahead of a newer one.
bundle_dirs_oldest_first() {
    local dir="$1"
    [[ -d "$dir" ]] || return 0
    find "$dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
        | sed -E 's/^(.*)-([0-9]{8}T[0-9]{6}Z)$/\2\t\1-\2/' \
        | sort \
        | cut -f2
}

# bundle_path_newest <dir> — absolute path of the newest bundle, or empty.
bundle_path_newest() {
    local dir="$1" newest
    newest="$(bundle_dirs_oldest_first "$dir" | tail -n1)"
    [[ -n "$newest" ]] || return 0
    printf '%s/%s\n' "$dir" "$newest"
}
