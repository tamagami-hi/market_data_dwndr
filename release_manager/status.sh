#!/usr/bin/env bash
# market_data_dwndr — release & sync console. Run from anywhere inside the repo.
#
# Modelled on boe_app/release_manager/status.sh, adapted to this repo:
#   • the version lives in backend/app/__init__.py (__version__), not a VERSION file
#   • releases are tagged vX.Y.Z (annotated) on main and pushed to origin
#   • the VPS deploy source is origin/main; the shippable artifact is a cut tag
#
# The DEFAULT run is REPORT-ONLY: it prints status + "what to do next" and changes
# nothing. Pass --interactive (on a terminal) to be prompted y/N before each action
# (commit, sync local↔remote main, cut a release, build/deploy/ship).
#
# Sections, always in this order:
#   1. MAIN — REMOTE   origin/main: the canonical version deploy.sh --ship ships.
#   2. MAIN — LOCAL    local main sync + branches waiting to be integrated.
#   3. CONTRIBUTORS    open PRs into main (review + approve; needs gh CLI).
#   4. RELEASE         current version, latest tag, staged build, rollbacks, VPS.
#
# Flags (default = --all):
#   --main            sections 1 + 2.
#   --contributors    section 3 (alias: --prs).
#   --release         section 4.
#   --vps <key>       query the live VPS-deployed version (SSH key path).
#                     alias: --remote <key>.
#   --interactive/-i  prompt y/N to EXECUTE actions (needs a terminal).
#   --read-only       force report-only (the default anyway).
#   --all [key]       everything (default). Optional trailing key queries the VPS.

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT_DIR" ]] || { echo "Not inside a git repo." >&2; exit 1; }
RELEASE_DIR="$ROOT_DIR/release_manager"
RELEASE_ENV="$RELEASE_DIR/.env"
RECENT_DIR="$RELEASE_DIR/recent_builds"
ROLLBACK_DIR="$RELEASE_DIR/rollback"

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/repo_sync.sh
source "$LIB_DIR/repo_sync.sh"
# shellcheck source=lib/version.sh
source "$LIB_DIR/version.sh"

# VPS target (host/user/dir from release_manager/.env; key from --vps/--remote or SHIP_KEY).
VPS_USER="$(env_value "$RELEASE_ENV" VPS_SSH_USER 2>/dev/null || true)"
VPS_HOST="$(env_value "$RELEASE_ENV" VPS_SSH_HOST 2>/dev/null || true)"
VPS_DIR="$(env_value "$RELEASE_ENV" VPS_DEPLOY_DIR 2>/dev/null || true)"
VPS_KEY="${SHIP_KEY:-}"

if [[ -t 1 ]]; then
    c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
else
    c_bold=''; c_dim=''; c_grn=''; c_yel=''; c_red=''; c_rst=''
fi
hdr() { printf '\n%s── %s ─────────────────────────────────────────%s\n' "$c_bold" "$1" "$c_rst"; }
count() { git -C "$ROOT_DIR" rev-list --count "$1" 2>/dev/null || echo 0; }

usage() {
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# ── argument parsing ──────────────────────────────────────────────────────────
SHOW_MAIN=false; SHOW_CONTRIB=false; SHOW_RELEASE=false
READ_ONLY=false; RUN=false
take_key() { [[ $# -ge 1 && "${1:-}" != -* && -n "${1:-}" ]] && { VPS_KEY="$1"; return 0; }; return 1; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --main)               SHOW_MAIN=true; shift ;;
        --contributors|--prs) SHOW_CONTRIB=true; shift ;;
        --release)            SHOW_RELEASE=true; shift ;;
        --vps|--remote)
            SHOW_RELEASE=true
            [[ $# -ge 2 && "${2:-}" != -* ]] || { echo "$1 requires a path to the SSH key" >&2; exit 1; }
            VPS_KEY="$2"; shift 2 ;;
        --interactive|-i) RUN=true; shift ;;
        --read-only) READ_ONLY=true; shift ;;
        --all)
            SHOW_MAIN=true; SHOW_CONTRIB=true; SHOW_RELEASE=true
            if take_key "${2:-}"; then shift 2; else shift; fi ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done
if [[ "$SHOW_MAIN" == false && "$SHOW_CONTRIB" == false && "$SHOW_RELEASE" == false ]]; then
    SHOW_MAIN=true; SHOW_CONTRIB=true; SHOW_RELEASE=true
fi

INTERACTIVE=false
if [[ "$RUN" == true && "$READ_ONLY" != true && -t 0 && -t 1 ]]; then INTERACTIVE=true; fi
if [[ "$RUN" == true && "$INTERACTIVE" != true ]]; then
    printf '%s--interactive given but no terminal to prompt on — showing read-only status.%s\n' "$c_yel" "$c_rst" >&2
fi

confirm() {
    local ans
    [[ "$INTERACTIVE" == true ]] || return 1
    printf '%s    ➜ %s [y/N] %s' "$c_bold" "$1" "$c_rst" >/dev/tty
    read -r ans </dev/tty || return 1
    [[ "$ans" == [yY] || "$ans" == [yY][eE][sS] ]]
}
# ask_run <prompt> <display-cmd> <argv...>
ask_run() {
    local prompt="$1" disp="$2"; shift 2
    if [[ "$INTERACTIVE" == true ]]; then
        if confirm "$prompt"; then
            printf '      %s$ %s%s\n' "$c_dim" "$disp" "$c_rst"
            if "$@"; then printf '      %s✓ done%s\n' "$c_grn" "$c_rst"
            else printf '      %s✗ failed — resolve manually%s\n' "$c_red" "$c_rst"; fi
        else
            printf '      %s· skipped%s\n' "$c_dim" "$c_rst"
        fi
    else
        printf '      %srun: %s%s\n' "$c_dim" "$disp" "$c_rst"
    fi
}
# ask_commit <message-default> — commit ALL changes in the repo (main worktree).
ask_commit() {
    local default_msg="$1" msg
    if [[ "$INTERACTIVE" != true ]]; then
        printf '      %scd %s && git add -A && git commit%s\n' "$c_dim" "$ROOT_DIR" "$c_rst"; return 0
    fi
    confirm "Commit all uncommitted changes on main?" || { printf '      %s· skipped%s\n' "$c_dim" "$c_rst"; return 0; }
    printf '%s    ➜ Commit message [Enter = "%s"]: %s' "$c_bold" "$default_msg" "$c_rst" >/dev/tty
    read -r msg </dev/tty || msg=""
    [[ -z "$msg" ]] && msg="$default_msg"
    if ( cd "$ROOT_DIR" && git add -A && git commit -q -m "$msg" ); then
        printf '      %s✓ committed%s\n' "$c_grn" "$c_rst"
    else
        printf '      %s✗ commit failed — resolve manually%s\n' "$c_red" "$c_rst"
    fi
}

# cut_release — tag the CURRENT version (from __init__.py) as vX.Y.Z and push
# main + tag. In this repo export.sh owns the version bump, so a "cut" simply
# stamps an immutable git tag on the released commit. Interactive only; the
# pipeline gates it on a clean, in-sync main so the tagged commit is on origin.
cut_release() {
    local current tagref
    current="$(canonical_version "$ROOT_DIR")"
    tagref="v$current"
    printf '%saction:%s tag the current version %s%s%s\n' "$c_bold" "$c_rst" "$c_grn" "$current" "$c_rst"
    if tag_exists "$ROOT_DIR" "$tagref"; then
        printf '      %s· %s is already tagged — nothing to cut%s\n' "$c_dim" "$tagref" "$c_rst"; return 0
    fi
    confirm "Tag current version $tagref on HEAD and push (main + tag)?" || {
        printf '      %s· skipped%s\n' "$c_dim" "$c_rst"; return 0; }
    printf '      %s$ git tag -a %s && git push origin main + %s%s\n' "$c_dim" "$tagref" "$tagref" "$c_rst"
    if git -C "$ROOT_DIR" tag -a "$tagref" -m "release $tagref" \
       && git -C "$ROOT_DIR" push -q origin main \
       && git -C "$ROOT_DIR" push -q origin "$tagref"; then
        printf '      %s✓ tagged %s and pushed to origin%s\n' "$c_grn" "$tagref" "$c_rst"
        printf '      %snote: the version bump lives in export.sh; this step tags what it produced%s\n' "$c_dim" "$c_rst"
    else
        printf '      %s✗ tagging failed. To undo a local-only tag: git -C %s tag -d %s%s\n' "$c_red" "$ROOT_DIR" "$tagref" "$c_rst"
        return 1
    fi
}

# ── header ─────────────────────────────────────────────────────────────────────
printf '%s═══ market_data_dwndr — release & sync console ═══%s\n' "$c_bold" "$c_rst"
if [[ "$INTERACTIVE" == true ]]; then
    printf '%sinteractive: y/N prompts will act on your repo (commit, sync, cut, build/ship)%s\n' "$c_dim" "$c_rst"
else
    printf '%sread-only (default): status + next steps. Re-run with --interactive to act.%s\n' "$c_dim" "$c_rst"
fi

repo_sync_eval "$ROOT_DIR"
local_branch="$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD || echo '?')"

# ── 1. MAIN — REMOTE ────────────────────────────────────────────────────────────
if [[ "$SHOW_MAIN" == true ]]; then
    hdr "1 · MAIN — REMOTE (origin/main → deploys to the VPS)"
    if [[ "$RS_HAS_REMOTE" == true ]]; then
        printf 'origin/main tip  : %s\n' "$(git -C "$ROOT_DIR" log -1 --format='%h  %s  (%cr)' origin/main 2>/dev/null)"
        printf 'deploy note      : %sthis is the tree deploy.sh --ship pushes to %s@%s%s\n' \
            "$c_dim" "${VPS_USER:-?}" "${VPS_HOST:-?}" "$c_rst"
    else
        printf 'origin/main      : %snot found on origin%s\n' "$c_yel" "$c_rst"
    fi
fi

# ── 2. MAIN — LOCAL ─────────────────────────────────────────────────────────────
CB_NAME=(); CB_AHEAD=()
if [[ "$SHOW_MAIN" == true ]]; then
    hdr "2 · MAIN — LOCAL (integrate → sync → push to remote main)"
    printf 'local main tip   : %s\n' "$(git -C "$ROOT_DIR" log -1 --format='%h  %s' main 2>/dev/null || echo '?')"
    printf 'on branch        : %s' "$local_branch"
    [[ "$local_branch" == "main" ]] && printf '  %s(on main)%s\n' "$c_grn" "$c_rst" \
                                    || printf '  %s(checkout main to integrate/cut)%s\n' "$c_yel" "$c_rst"
    repo_sync_notice

    # Remote branches ahead of local main = contributor work to integrate.
    while read -r rb; do
        [[ "$rb" == "main" || -z "$rb" ]] && continue
        a="$(count "main..origin/$rb")"
        [[ "$a" -gt 0 ]] && { CB_NAME+=("$rb"); CB_AHEAD+=("$a"); }
    done < <(git -C "$ROOT_DIR" for-each-ref --format='%(refname)' refs/remotes/origin \
                | grep -v '/HEAD$' | sed 's#^refs/remotes/origin/##')
    if [[ "${#CB_NAME[@]}" -eq 0 ]]; then
        printf 'to integrate     : %snone — local main has all remote-branch commits%s\n' "$c_dim" "$c_rst"
    else
        printf 'to integrate     :\n'
        for i in "${!CB_NAME[@]}"; do
            printf '  origin/%-14s %s%s commit(s) ahead of local main%s\n' "${CB_NAME[$i]}" "$c_yel" "${CB_AHEAD[$i]}" "$c_rst"
        done
    fi
fi

# ── 3. CONTRIBUTORS (PRs) ───────────────────────────────────────────────────────
if [[ "$SHOW_CONTRIB" == true ]]; then
    hdr "3 · CONTRIBUTORS (open PRs → main · review + approve)"
    if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
        repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
        printf 'repo             : %s\n' "${repo:-unknown}"
        P_NUM=(); P_BR=(); P_AUTHOR=(); P_DEC=()
        while IFS=$'\t' read -r num br author dec; do
            [[ -z "$num" ]] && continue
            P_NUM+=("$num"); P_BR+=("$br"); P_AUTHOR+=("$author"); P_DEC+=("$dec")
        done < <(gh pr list --base main --state open \
                    --json number,headRefName,author,reviewDecision \
                    --jq '.[] | [.number, .headRefName, .author.login, (.reviewDecision // "REVIEW_REQUIRED")] | @tsv' 2>/dev/null)
        if [[ "${#P_NUM[@]}" -eq 0 ]]; then
            printf 'open PRs → main  : %s(none)%s\n' "$c_dim" "$c_rst"
        else
            printf 'open PRs → main  :\n'
            for i in "${!P_NUM[@]}"; do
                if [[ "${P_DEC[$i]}" == "APPROVED" ]]; then ac="$c_grn"; else ac="$c_yel"; fi
                printf '  #%-4s %-18s %-14s %s%s%s\n' "${P_NUM[$i]}" "${P_BR[$i]}" "${P_AUTHOR[$i]}" "$ac" "${P_DEC[$i]}" "$c_rst"
            done
            for i in "${!P_NUM[@]}"; do
                [[ "${P_DEC[$i]}" == "APPROVED" ]] && continue
                ask_run "Approve PR #${P_NUM[$i]} (${P_BR[$i]})?" \
                        "gh pr review ${P_NUM[$i]} --approve" \
                        gh pr review "${P_NUM[$i]}" --approve
            done
        fi
    else
        printf '%sgh CLI missing or not authenticated — PR review skipped.%s\n' "$c_yel" "$c_rst"
        printf '%sInstall https://cli.github.com then: gh auth login%s\n' "$c_dim" "$c_rst"
    fi
fi

# ── 4. RELEASE ──────────────────────────────────────────────────────────────────
cur_ver=""; last_tag=""
if [[ "$SHOW_RELEASE" == true ]]; then
    hdr "4 · RELEASE (version · tag · staged build · VPS)"
    cur_ver="$(canonical_version "$ROOT_DIR")"
    last_tag="$(latest_tag "$ROOT_DIR")"
    printf 'version (__init__): %s%s%s\n' "$c_grn" "$cur_ver" "$c_rst"
    if [[ -n "$last_tag" ]]; then
        if [[ "$last_tag" == "v$cur_ver" ]]; then
            if on_exact_release_tag "$ROOT_DIR" "$cur_ver"; then
                printf 'latest tag       : %s%s%s  %s(HEAD is on this tag, tree clean — shippable)%s\n' "$c_grn" "$last_tag" "$c_rst" "$c_dim" "$c_rst"
            else
                printf 'latest tag       : %s%s%s  %s(matches version, but HEAD/tree moved — cut or commit)%s\n' "$c_yel" "$last_tag" "$c_rst" "$c_dim" "$c_rst"
            fi
        else
            printf 'latest tag       : %s%s%s  %s(version %s is NOT tagged yet — cut a release)%s\n' "$c_yel" "$last_tag" "$c_rst" "$c_dim" "$cur_ver" "$c_rst"
        fi
    else
        printf 'latest tag       : %s(none yet)%s\n' "$c_yel" "$c_rst"
    fi

    staged="$(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '.export-*' 2>/dev/null | sort | tail -1)"
    [[ -n "$staged" ]] && printf 'staged build     : %s%s%s\n' "$c_grn" "$(basename "$staged")" "$c_rst" \
                        || printf 'staged build     : %s(none)%s\n' "$c_dim" "$c_rst"
    printf 'rollbacks        : %s\n' "$(find "$ROLLBACK_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"

    # Live VPS-deployed version (from version.json), if a key was supplied.
    if [[ -n "$VPS_KEY" ]]; then
        if [[ ! -f "$VPS_KEY" ]]; then
            printf 'vps deployed     : %skey not found: %s%s\n' "$c_yel" "$VPS_KEY" "$c_rst"
        elif [[ -z "$VPS_USER" || -z "$VPS_HOST" || -z "$VPS_DIR" ]]; then
            printf 'vps deployed     : %sVPS_SSH_USER/HOST/DEPLOY_DIR missing in release_manager/.env%s\n' "$c_yel" "$c_rst"
        else
            vps_ver="$(ssh -i "$VPS_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 \
                "$VPS_USER@$VPS_HOST" "cat $VPS_DIR/version.json 2>/dev/null" 2>/dev/null \
                | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
            if [[ -z "$vps_ver" ]]; then
                printf 'vps deployed     : %sunreachable or no release%s  %s(%s@%s)%s\n' "$c_yel" "$c_rst" "$c_dim" "$VPS_USER" "$VPS_HOST" "$c_rst"
            elif [[ "$vps_ver" == "v$cur_ver" ]]; then
                printf 'vps deployed     : %s%s%s  %s(in sync with local)%s\n' "$c_grn" "$vps_ver" "$c_rst" "$c_dim" "$c_rst"
            else
                printf 'vps deployed     : %s%s%s  %s(local is v%s)%s\n' "$c_yel" "$vps_ver" "$c_rst" "$c_dim" "$cur_ver" "$c_rst"
            fi
        fi
    else
        printf 'vps deployed     : %s(pass an SSH key to query: --vps <key>)%s\n' "$c_dim" "$c_rst"
    fi
fi

# ── DO THIS NEXT — the interactive action pipeline, in dependency order ──────────
hdr "DO THIS NEXT"
step=1
say() { printf '\n %s%s.%s %s\n' "$c_bold" "$step" "$c_rst" "$1"; step=$((step+1)); }
cmd() { printf '      %s%s%s\n' "$c_dim" "$1" "$c_rst"; }

# 1 · commit uncommitted work on main.
if [[ "$RS_DIRTY" -gt 0 ]]; then
    say "Local main has $RS_DIRTY uncommitted change(s) — commit them:"
    if [[ "$local_branch" == "main" ]]; then ask_commit "chore: update working tree"
    else cmd "checkout main first: git -C $ROOT_DIR checkout main"; fi
fi

# 2 · integrate remote branches into local main.
if [[ "$SHOW_MAIN" == true && "${#CB_NAME[@]}" -gt 0 ]]; then
    say "Integrate remote-branch work into local main:"
    if [[ "$local_branch" != "main" ]]; then
        cmd "checkout main first: git -C $ROOT_DIR checkout main"
    else
        for i in "${!CB_NAME[@]}"; do
            ask_run "Merge origin/${CB_NAME[$i]} into local main (${CB_AHEAD[$i]} commit(s))?" \
                    "git -C $ROOT_DIR merge --no-edit origin/${CB_NAME[$i]}" \
                    git -C "$ROOT_DIR" merge --no-edit "origin/${CB_NAME[$i]}"
        done
    fi
fi

# 3 · sync local main ↔ remote main.
if [[ "$SHOW_MAIN" == true && "$RS_HAS_REMOTE" == true ]]; then
    [[ "$INTERACTIVE" == true ]] && repo_sync_eval "$ROOT_DIR"
    if [[ "$RS_BEHIND" -gt 0 || "$RS_AHEAD" -gt 0 ]]; then
        say "Sync local main ↔ remote main:"
        if [[ "$RS_BEHIND" -gt 0 ]]; then
            [[ "$RS_DIRTY" -gt 0 ]] && cmd "note: commit/stash first — rebase refuses a dirty tree"
            ask_run "Pull --rebase remote main → local (behind by $RS_BEHIND)?" \
                    "git -C $ROOT_DIR pull --rebase origin main" \
                    git -C "$ROOT_DIR" pull --rebase origin main
            [[ "$INTERACTIVE" == true ]] && repo_sync_eval "$ROOT_DIR"
        fi
        if [[ "$RS_AHEAD" -gt 0 ]]; then
            ask_run "Push local main → remote main (ahead by $RS_AHEAD)?" \
                    "git -C $ROOT_DIR push origin main" \
                    git -C "$ROOT_DIR" push origin main
        fi
    else
        printf '\n %s· local main is level with remote main — in sync.%s\n' "$c_dim" "$c_rst"
    fi
fi

# 4 · cut a stable release (bump __version__ → commit → tag → push).
if [[ "$SHOW_RELEASE" == true || "$SHOW_MAIN" == true ]]; then
    say "Cut a stable release (tag the current version vX.Y.Z, push):"
    if [[ "$INTERACTIVE" == true && "$local_branch" == "main" && "$RS_HAS_REMOTE" == true ]]; then
        repo_sync_eval "$ROOT_DIR"
        if [[ "$RS_CLEAN_SYNC" == true ]]; then
            cut_release || true
        else
            printf '      %s· main is not clean + in sync yet — finish the steps above, then re-run to cut.%s\n' "$c_dim" "$c_rst"
        fi
    else
        cmd "cd $ROOT_DIR && ./release_manager/status.sh --interactive   # answer the cut prompt"
    fi
fi

# 5 · build / deploy / ship.
if [[ "$SHOW_RELEASE" == true ]]; then
    cur_ver="$(canonical_version "$ROOT_DIR")"
    if on_exact_release_tag "$ROOT_DIR" "$cur_ver"; then
        printf '\n %s✓ clean tree on tag v%s — ready to build + ship.%s\n' "$c_grn" "$cur_ver" "$c_rst"
    else
        printf '\n %s⚠ not on a clean release tag — cut a release first so HEAD == v%s (build will still work locally).%s\n' \
            "$c_yel" "$cur_ver" "$c_rst"
    fi
    say "Build the release bundle from the current tree:"
    ask_run "Build the release bundle now (export.sh)?" \
            "cd $ROOT_DIR && ./release_manager/export.sh" \
            "$RELEASE_DIR/export.sh"
    say "Deploy locally to test (compose up, health-checked):"
    ask_run "Deploy the staged bundle locally now (deploy.sh)?" \
            "cd $ROOT_DIR && ./release_manager/deploy.sh" \
            "$RELEASE_DIR/deploy.sh"
    say "Ship the staged bundle to the VPS (after a local test):"
    if [[ -n "$VPS_KEY" && -f "$VPS_KEY" ]]; then
        ask_run "Ship to the VPS now (deploy.sh --ship)?" \
                "cd $ROOT_DIR && ./release_manager/deploy.sh --ship $VPS_KEY" \
                "$RELEASE_DIR/deploy.sh" --ship "$VPS_KEY"
    else
        cmd "cd $ROOT_DIR && ./release_manager/deploy.sh --ship <key>"
        printf '      %s(pass the key to enable this prompt: --all <key> or --vps <key>)%s\n' "$c_dim" "$c_rst"
    fi
fi

printf '\n%sFlow:%s commit → integrate branches → sync local↔remote main → cut (tag vX.Y.Z) → export → deploy (local test) → ship to VPS.\n' "$c_dim" "$c_rst"
exit 0
