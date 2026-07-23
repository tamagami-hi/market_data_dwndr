#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# version.sh — shared semver helpers for the release tooling (market_data_dwndr).
#
# Sourced by status.sh (which CUTS releases: bump → commit → tag → push) and
# export.sh (which only READS the canonical version to label a build). The single
# source of truth for the version is backend/app/__init__.py (__version__).
#
# All functions are pure unless noted; the ones that read git take the repo dir.
# `set -u`-safe throughout.
# ─────────────────────────────────────────────────────────────────────────────

# Path to the file that holds __version__ = "X.Y.Z".
pkg_version_file() { printf '%s/backend/app/__init__.py' "$1"; }

# read_pkg_version <repo> — echo the bare X.Y.Z from __init__.py, or 0.0.0.
read_pkg_version() {
    local f; f="$(pkg_version_file "$1")"
    if [[ -f "$f" ]]; then
        sed -n 's/^__version__ = "\(.*\)"/\1/p' "$f" | head -n 1 | tr -d '[:space:]'
    else
        printf '0.0.0'
    fi
}

# write_pkg_version <repo> <version> — rewrite __version__ in __init__.py.
write_pkg_version() {
    local f; f="$(pkg_version_file "$1")"
    [[ -f "$f" ]] || { printf 'version file not found: %s\n' "$f" >&2; return 1; }
    sed -i "s/^__version__ = .*/__version__ = \"$2\"/" "$f"
}

# assert_semver <version> — non-zero unless bare X.Y.Z.
assert_semver() {
    [[ "$1" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]] \
        || { printf 'Version must be numeric X.Y.Z, got: %s\n' "$1" >&2; return 1; }
}

# bump_version <version> <patch|minor|major> — echo the bumped X.Y.Z.
bump_version() {
    local version="$1" bump="$2" major minor patch
    assert_semver "$version" || return 1
    IFS=. read -r major minor patch <<<"$version"
    major="${major:-0}"; minor="${minor:-0}"; patch="${patch:-0}"
    case "$bump" in
        major) major=$((major + 1)); minor=0; patch=0 ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        patch|"") patch=$((patch + 1)) ;;
        *) printf 'Unsupported bump: %s\n' "$bump" >&2; return 1 ;;
    esac
    printf '%s.%s.%s\n' "$major" "$minor" "$patch"
}

# latest_tag <repo> — most recent vX.Y.Z tag (empty if none).
latest_tag() { git -C "$1" describe --tags --abbrev=0 2>/dev/null || true; }

# canonical_version <repo> — the current version, in priority order:
#   __init__.py __version__ → latest git tag (minus v) → 0.0.0
canonical_version() {
    local repo="$1" v t
    v="$(read_pkg_version "$repo")"
    if [[ -n "$v" && "$v" != "0.0.0" ]]; then printf '%s\n' "$v"; return 0; fi
    t="$(latest_tag "$repo")"; [[ -n "$t" ]] && printf '%s\n' "${t#v}" || printf '0.0.0\n'
}

# on_exact_release_tag <repo> <version> — true iff the tree is clean AND HEAD is
# exactly the v<version> tag (i.e. the build comes from a cut release commit).
on_exact_release_tag() {
    local repo="$1" version="$2" exact
    [[ -z "$(git -C "$repo" status --porcelain 2>/dev/null)" ]] || return 1
    exact="$(git -C "$repo" describe --exact-match --tags HEAD 2>/dev/null || true)"
    [[ "$exact" == "v$version" ]]
}

# tag_exists <repo> <tagref> — true if the tag already exists locally.
tag_exists() { git -C "$1" rev-parse -q --verify "refs/tags/$2" >/dev/null 2>&1; }
