#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_release.sh — cut a release: one version in backend/app/__init__.py and a
# git tag, moved together and pushed together, so they never disagree.
#
#   release patch|minor|major [--yes] [--no-push]
#
#   1. Refuse unless HEAD is main, the tree is clean, main is not behind
#      origin/main, and the next version's tag is new.
#   2. Bump __version__ in backend/app/__init__.py.
#   3. Commit exactly that file: "chore(release): vX.Y.Z".
#   4. Tag that commit vX.Y.Z (annotated).
#   5. Push main and the tag in one atomic push (unless --no-push).
#
# Releases come from main only: origin/main is what ships. Nothing is built:
# Export on that commit then builds X.Y.Z instead of a dev label. The commit
# uses the repository's git identity.
# ─────────────────────────────────────────────────────────────────────────────

RELEASE_BRANCH=main
RELEASE_REMOTE=origin
RELEASE_FILE=backend/app/__init__.py

# release_target <current> <patch|minor|major> — the version a release cuts.
release_target() {
    case "$2" in
        major|minor|patch) bump_version "$1" "$2" ;;
        *) return 1 ;;
    esac
}

# release_up_to_date <root> — fetch, then refuse if origin/main has commits
# this main lacks (the push would be refused).
release_up_to_date() {
    local root="$1"
    git -C "$root" fetch --quiet "$RELEASE_REMOTE" 2>/dev/null \
        || { err "could not reach $RELEASE_REMOTE; nothing changed"; return 1; }
    if git -C "$root" rev-parse -q --verify "refs/remotes/$RELEASE_REMOTE/$RELEASE_BRANCH" >/dev/null; then
        git -C "$root" merge-base --is-ancestor "refs/remotes/$RELEASE_REMOTE/$RELEASE_BRANCH" HEAD \
            || { err "$RELEASE_REMOTE/$RELEASE_BRANCH has commits main lacks; pull --rebase first"; return 1; }
    fi
}

# Puts the version file back when a release stops before its commit.
restore_versions() {
    git -C "$ROOT_DIR" checkout -q -- "$RELEASE_FILE"
}

op_release() {
    local part='' yes=false push=true
    while (($#)); do
        case "$1" in
            major|minor|patch) part="$1"; shift ;;
            --yes) yes=true; shift ;;
            --no-push) push=false; shift ;;
            *) err "unknown release option: $1 (patch, minor or major, then --yes or --no-push)"; exit 1 ;;
        esac
    done
    [[ -n "$part" ]] || { err "say which part to bump: release patch|minor|major"; exit 1; }
    command -v git >/dev/null || { err "git is required"; exit 1; }

    local current target latest branch
    current="$(pkg_version "$ROOT_DIR")" || exit 1
    assert_semver "$current" || exit 1
    target="$(release_target "$current" "$part")" || exit 1
    latest="$(latest_release_tag "$ROOT_DIR")"

    banner "RELEASE · $part · v$target"
    field "__version__" "$current → $target"
    field "latest tag"  "$(release_label "$latest")"

    # ── preconditions: nothing changes unless all of them hold ──────────────
    branch="$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD 2>/dev/null)" \
        || { err "HEAD is detached; check out $RELEASE_BRANCH to release"; exit 1; }
    field "branch"      "$branch"
    [[ "$branch" == "$RELEASE_BRANCH" ]] \
        || { err "releases are cut from $RELEASE_BRANCH only; this is $branch"; exit 1; }
    if git_dirty "$ROOT_DIR"; then
        err "the tree has uncommitted changes; commit or stash them first"
        exit 1
    fi
    if git -C "$ROOT_DIR" rev-parse -q --verify "refs/tags/v$target" >/dev/null; then
        err "tag v$target already exists; __version__ and the tags disagree"
        exit 1
    fi
    if [[ -n "$latest" ]] && ! version_gt "$target" "$latest"; then
        err "v$target is not above the latest release v$latest; __version__ is behind the tags"
        exit 1
    fi
    if [[ "$push" == true ]]; then
        git -C "$ROOT_DIR" remote get-url "$RELEASE_REMOTE" >/dev/null 2>&1 \
            || { err "no $RELEASE_REMOTE remote to push to (or use --no-push)"; exit 1; }
        field "push to"     "$RELEASE_REMOTE $RELEASE_BRANCH"
        release_up_to_date "$ROOT_DIR" || exit 1
    else
        field "push to"     "nowhere (--no-push)"
    fi

    if [[ "$yes" != true ]]; then
        [[ "$UI_INTERACTIVE" == true ]] || { err "no terminal to confirm on: add --yes"; exit 1; }
        local what="bump $current → $target, commit and tag v$target on $branch"
        [[ "$push" == true ]] && what="$what, then push to $RELEASE_REMOTE"
        confirm "$what?" || { warn "cancelled"; exit 1; }
    fi

    # ── version file and the release commit ────────────────────────────────
    step "$RELEASE_FILE → $target"
    set_pkg_version "$ROOT_DIR" "$target" || { err "could not rewrite $RELEASE_FILE"; restore_versions; exit 1; }
    [[ "$(pkg_version "$ROOT_DIR")" == "$target" ]] \
        || { err "$RELEASE_FILE did not take the new version"; restore_versions; exit 1; }

    local changed
    changed="$(git -C "$ROOT_DIR" status --porcelain | awk '{print $2}' | sort | tr '\n' ' ')"
    if [[ "$changed" != "$RELEASE_FILE " ]]; then
        err "the release touched more than $RELEASE_FILE: $changed"
        restore_versions
        exit 1
    fi
    git -C "$ROOT_DIR" add -- "$RELEASE_FILE"
    git -C "$ROOT_DIR" commit -q -m "chore(release): v$target" \
        -m "A $part release: __version__ moves from $current to $target, and this commit is tagged v$target, so Export builds $target rather than a dev label." \
        || { err "the release commit failed"; git -C "$ROOT_DIR" reset -q HEAD -- "$RELEASE_FILE"; restore_versions; exit 1; }
    ok "committed $(git -C "$ROOT_DIR" rev-parse --short=9 HEAD)"

    git -C "$ROOT_DIR" tag -a "v$target" -m "v$target" || { err "could not create the tag"; exit 1; }
    ok "tagged v$target"

    # ── push: main and the tag land together or not at all ─────────────────
    if [[ "$push" == true ]]; then
        step "pushing $RELEASE_BRANCH and v$target to $RELEASE_REMOTE"
        if git -C "$ROOT_DIR" push --quiet --atomic "$RELEASE_REMOTE" \
            "HEAD:refs/heads/$RELEASE_BRANCH" "refs/tags/v$target"; then
            ok "pushed $RELEASE_BRANCH and v$target to $RELEASE_REMOTE"
        else
            err "the push failed; the release commit and tag are kept here"
            info "retry with: git push --atomic $RELEASE_REMOTE HEAD:refs/heads/$RELEASE_BRANCH refs/tags/v$target"
            exit 1
        fi
    fi

    banner "RELEASE READY · v$target"
    info "Export on this commit builds $target."
    [[ "$push" == true ]] || info "Not pushed; to publish: git push --atomic $RELEASE_REMOTE HEAD:refs/heads/$RELEASE_BRANCH refs/tags/v$target"
}
