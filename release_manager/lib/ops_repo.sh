#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_repo.sh — keeping main ready to release, from this computer.
#
# Releases are cut from main only and origin/main is what ships, so the
# Repository workflow brings work onto main: commit the working tree, merge a
# remote branch that is ahead of main, sync main with origin/main, and approve
# open pull requests into main. Each action says what it will run and asks
# first; none runs without a terminal unless --yes is given.
#
# repo_eval fills the RS_* globals the dashboard reads. Nothing in it writes
# to the repository; it only fetches.
# ─────────────────────────────────────────────────────────────────────────────

# repo_eval — fetch origin (best effort) and compare main with origin/main.
#   RS_FETCHED  RS_HAS_REMOTE  RS_AHEAD  RS_BEHIND  RS_DIRTY
repo_eval() {
    local g=(git -C "$ROOT_DIR")
    RS_FETCHED=true
    "${g[@]}" fetch -q origin 2>/dev/null || RS_FETCHED=false
    if "${g[@]}" rev-parse -q --verify refs/remotes/origin/main >/dev/null; then
        RS_HAS_REMOTE=true
        RS_AHEAD="$("${g[@]}" rev-list --count origin/main..main 2>/dev/null || echo 0)"
        RS_BEHIND="$("${g[@]}" rev-list --count main..origin/main 2>/dev/null || echo 0)"
    else
        RS_HAS_REMOTE=false; RS_AHEAD=0; RS_BEHIND=0
    fi
    RS_DIRTY="$("${g[@]}" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
}

# repo_branches_ahead — "branch<TAB>count" for each remote branch with commits
# main lacks: work waiting to be integrated.
repo_branches_ahead() {
    local rb ahead
    while IFS= read -r rb; do
        [[ -n "$rb" && "$rb" != main ]] || continue
        ahead="$(git -C "$ROOT_DIR" rev-list --count "main..origin/$rb" 2>/dev/null || echo 0)"
        (( ahead > 0 )) && printf '%s\t%s\n' "$rb" "$ahead"
    done < <(git -C "$ROOT_DIR" for-each-ref --format='%(refname)' refs/remotes/origin \
                | grep -v '/HEAD$' | sed 's#^refs/remotes/origin/##')
    return 0
}

repo_on_main() {
    [[ "$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD 2>/dev/null)" == main ]] \
        || { err "check out main first: git -C $ROOT_DIR checkout main"; return 1; }
}

repo_confirm() {
    local yes="$1" what="$2"
    [[ "$yes" == true ]] && return 0
    [[ "$UI_INTERACTIVE" == true ]] || { err "no terminal to confirm on: add --yes"; return 1; }
    confirm "$what?" || { warn "cancelled"; return 1; }
}

# op_repo_commit [--yes] [--message TEXT] — commit every change on main.
op_repo_commit() {
    local yes=false message=''
    while (($#)); do
        case "$1" in
            --yes) yes=true; shift ;;
            --message) message="${2:-}"; shift 2 ;;
            *) err "unknown commit option: $1"; exit 1 ;;
        esac
    done
    repo_on_main || exit 1
    git_dirty "$ROOT_DIR" || { ok "nothing to commit"; return 0; }
    git -C "$ROOT_DIR" status --short | sed 's/^/     /'
    if [[ -z "$message" ]]; then
        [[ "$UI_INTERACTIVE" == true ]] || { err "no terminal: pass --message"; exit 1; }
        printf '%s   ➜ commit message: %s' "$c_bold" "$c_rst"
        read -r message || message=''
        [[ -n "$message" ]] || { warn "no message, nothing committed"; exit 1; }
    fi
    repo_confirm "$yes" "commit all of the above on main as \"$message\"" || exit 1
    git -C "$ROOT_DIR" add -A && git -C "$ROOT_DIR" commit -q -m "$message" \
        || { err "the commit failed"; exit 1; }
    ok "committed $(git -C "$ROOT_DIR" rev-parse --short=9 HEAD)"
}

# op_repo_integrate <branch> [--yes] — merge origin/<branch> into main.
op_repo_integrate() {
    local branch='' yes=false
    while (($#)); do
        case "$1" in
            --yes) yes=true; shift ;;
            *) branch="$1"; shift ;;
        esac
    done
    [[ "$branch" =~ ^[A-Za-z0-9._/-]+$ && "$branch" != -* ]] || { err "name a remote branch to merge"; exit 1; }
    repo_on_main || exit 1
    git_dirty "$ROOT_DIR" && { err "commit or stash first: a merge refuses a dirty tree"; exit 1; }
    git -C "$ROOT_DIR" rev-parse -q --verify "refs/remotes/origin/$branch" >/dev/null \
        || { err "no such remote branch: origin/$branch"; exit 1; }
    git -C "$ROOT_DIR" log --oneline "main..origin/$branch" | sed 's/^/     /'
    repo_confirm "$yes" "merge origin/$branch into main" || exit 1
    git -C "$ROOT_DIR" merge --no-edit "origin/$branch" || { err "the merge stopped; resolve it by hand"; exit 1; }
    ok "merged origin/$branch"
}

# op_repo_sync [--yes] — pull --rebase origin/main, then push main.
op_repo_sync() {
    local yes=false
    [[ "${1:-}" == --yes ]] && yes=true
    repo_on_main || exit 1
    repo_eval
    [[ "$RS_HAS_REMOTE" == true ]] || { err "origin/main not found"; exit 1; }
    if (( RS_BEHIND > 0 )); then
        git_dirty "$ROOT_DIR" && { err "commit or stash first: a rebase refuses a dirty tree"; exit 1; }
        repo_confirm "$yes" "pull --rebase $RS_BEHIND commit(s) from origin/main" || exit 1
        git -C "$ROOT_DIR" pull -q --rebase origin main || { err "the rebase stopped; resolve it by hand"; exit 1; }
        ok "main rebased on origin/main"
        repo_eval
    fi
    if (( RS_AHEAD > 0 )); then
        repo_confirm "$yes" "push $RS_AHEAD commit(s) from main to origin/main" || exit 1
        git -C "$ROOT_DIR" push -q origin main || { err "the push failed"; exit 1; }
        ok "pushed main to origin"
    fi
    (( RS_BEHIND == 0 && RS_AHEAD == 0 )) && ok "main is level with origin/main"
    return 0
}

# repo_prs — "number<TAB>branch<TAB>author<TAB>decision" for open PRs into main.
repo_prs() {
    command -v gh >/dev/null && gh auth status >/dev/null 2>&1 || return 1
    (cd "$ROOT_DIR" && gh pr list --base main --state open \
        --json number,headRefName,author,reviewDecision \
        --jq '.[] | [.number, .headRefName, .author.login, (.reviewDecision // "REVIEW_REQUIRED")] | @tsv' 2>/dev/null)
}

# op_repo_prs [--approve N] [--yes] — list open PRs into main, or approve one.
op_repo_prs() {
    local approve='' yes=false
    while (($#)); do
        case "$1" in
            --approve) approve="${2:-}"; shift 2 ;;
            --yes) yes=true; shift ;;
            *) err "unknown prs option: $1"; exit 1 ;;
        esac
    done
    local prs
    prs="$(repo_prs)" || { warn "gh is missing or not signed in (gh auth login) — PRs skipped"; return 0; }
    if [[ -z "$prs" ]]; then
        info "no open pull requests into main"
    else
        while IFS=$'\t' read -r num br author decision; do
            printf '     #%-5s %-22s %-16s %s\n' "$num" "$br" "$author" "$decision"
        done <<<"$prs"
    fi
    [[ -n "$approve" ]] || return 0
    [[ "$approve" =~ ^[0-9]+$ ]] || { err "--approve needs a PR number"; exit 1; }
    repo_confirm "$yes" "approve PR #$approve" || exit 1
    (cd "$ROOT_DIR" && gh pr review "$approve" --approve) || { err "the approval failed"; exit 1; }
    ok "approved PR #$approve"
}
