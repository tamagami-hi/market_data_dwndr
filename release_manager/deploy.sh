#!/usr/bin/env bash
# Build-machine deploy:
#   ./release_manager/deploy.sh              -> compose up HERE (local stack)
#   ./release_manager/deploy.sh --ship KEY   -> rsync the staged bundle to the VPS
#                                               (preserving the VPS .env) and run
#                                               the shipped DATA_DOWNLOADER/deploy.sh
#   ./release_manager/deploy.sh --force --ship KEY
#                                            -> FORCE UPDATE: force-deploy the local
#                                               stack, then ship + force-deploy on the
#                                               VPS. Bypasses the market-window and
#                                               "capture must be stopped" guards and
#                                               recreates containers even if the version
#                                               is unchanged. Writers are still drained
#                                               via the maintenance lease before the
#                                               swap, and health-check + auto-rollback
#                                               still apply. Use when the running stack
#                                               is stuck/frozen and must be replaced now.
#
# Local paths are script-driven and version-controlled (see LOCAL_* below). The
# VPS is fully env-driven via its own DATA_DOWNLOADER/.env.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release_manager"
RECENT_DIR="$RELEASE_DIR/recent_builds"
RELEASE_ENV="$RELEASE_DIR/.env"
BACKEND_ENV="$ROOT_DIR/backend/.env"
FRONTEND_ENV="$ROOT_DIR/frontend/.env.local"
BUNDLE_DIR=""
SHIP_KEY=""
FORCE=false
DOCKER=()

# Script-driven, version-controlled local stack paths (used only for local up).
LOCAL_STACK_ROOT="$ROOT_DIR/.local_stack"
LOCAL_MARKET_DATA="$RELEASE_DIR/DATA_DOWNLOADER/MARKET_DATA"
LOCAL_ARCHIVE_DATA="$LOCAL_STACK_ROOT/z_market_data"

# shellcheck source=lib/common.sh
source "$RELEASE_DIR/lib/common.sh"

usage() {
    cat <<'USAGE'
Usage: ./release_manager/deploy.sh [--frontend|--backend] [--bundle DIR]
       ./release_manager/deploy.sh --ship SSH_KEY [--frontend|--backend] [--bundle DIR]
       ./release_manager/deploy.sh --force [--ship SSH_KEY] [--frontend|--backend]

  --frontend  Deploy ONLY the frontend service (recreated with --no-deps, so the
              backend container and any running capture are left completely alone).
              Because it cannot disturb capture, this is allowed DURING market hours
              and does not take the writer-drain maintenance lease.
  --backend   Deploy ONLY the backend service. This DOES interrupt capture, so the
              market-window and capture-stopped guards still apply — combine with
              --force for an urgent backend fix inside market hours.
              Omitting both deploys whatever the bundle contains (the default).

  --force     Force update: bypass the market-window and capture-stopped guards and
              recreate containers even if the version is unchanged. With --ship it
              force-deploys locally first, then ships and force-deploys on the VPS.
USAGE
}

want_frontend=false
want_backend=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE_DIR="${2:?--bundle requires a directory}"; shift 2 ;;
        --ship) SHIP_KEY="${2:?--ship requires an SSH key}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        --frontend) want_frontend=true; shift ;;
        --backend) want_backend=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

# Services the caller asked for; empty means "whatever the bundle ships".
REQUESTED=()
[[ "$want_backend" == true ]] && REQUESTED+=(backend)
[[ "$want_frontend" == true ]] && REQUESTED+=(frontend)

# Resolved at deploy time against the bundle contents (see resolve_services).
SERVICES=()

# Intersect what the caller asked for with what the bundle actually contains, so we can
# never try to deploy a service whose image was not built.
resolve_services() {
    local bundle_dir=$1 available=() svc
    mapfile -t available < <(bundle_services "$bundle_dir")
    if [[ ${#REQUESTED[@]} -eq 0 ]]; then
        SERVICES=("${available[@]}")
        return
    fi
    SERVICES=()
    for svc in "${REQUESTED[@]}"; do
        if [[ " ${available[*]} " == *" $svc "* ]]; then
            SERVICES+=("$svc")
        else
            printf 'Bundle %s does not contain the %s image (it ships: %s). Re-export with --%s.\n' \
                "$(basename "$bundle_dir")" "$svc" "${available[*]}" "$svc" >&2
            exit 1
        fi
    done
}

resolve_bundle() {
    [[ -z "$BUNDLE_DIR" ]] || { BUNDLE_DIR="$(cd "$BUNDLE_DIR" && pwd)"; return; }
    local -a bundles=()
    mapfile -t bundles < <(find "$RECENT_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '.export-*' -print | sort)
    [[ ${#bundles[@]} -eq 1 ]] || {
        printf 'Expected exactly one staged bundle in %s (found %s). Use --bundle.\n' \
            "$RECENT_DIR" "${#bundles[@]}" >&2; exit 1;
    }
    BUNDLE_DIR="${bundles[0]}"
}

release_config() {
    local key=$1 value="${!1:-}"
    [[ -n "$value" || ! -f "$RELEASE_ENV" ]] || value="$(env_value "$RELEASE_ENV" "$key")"
    printf '%s' "$value"
}

copy_active_bundle_to_rollback() {
    local active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    [[ -f "$active_dir/version.json" ]] || { printf 'No active release yet — nothing to snapshot.\n'; return 0; }
    [[ -f "$active_dir/images/backend.tar.gz" ]] || { printf 'Active release has no images — nothing to snapshot.\n'; return 0; }

    local version stamp snapshot_dir
    version="$(jq -r '.version' "$active_dir/version.json" 2>/dev/null || echo unknown)"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    snapshot_dir="$RELEASE_DIR/rollback/${version:-unknown}-${stamp}"
    mkdir -p "$snapshot_dir"

    for file in docker-compose.yml .env version.json manifest.json README.md deploy.sh rollback.sh; do
        [[ -f "$active_dir/$file" ]] && cp "$active_dir/$file" "$snapshot_dir/$file"
    done
    [[ -f "$active_dir/images/backend.tar.gz" ]] && cp "$active_dir/images/backend.tar.gz" "$snapshot_dir/backend.tar.gz"
    [[ -f "$active_dir/images/frontend.tar.gz" ]] && cp "$active_dir/images/frontend.tar.gz" "$snapshot_dir/frontend.tar.gz"
    
    printf 'Snapshotted active release %s -> rollback/%s\n' "${version:-unknown}" "$(basename "$snapshot_dir")"
}

deploy_local() {
    command -v git >/dev/null || { echo "git is required." >&2; exit 1; }
    command -v jq >/dev/null || { echo "jq is required." >&2; exit 1; }
    mapfile -d '' -t DOCKER < <(docker_engine_command)
    "${DOCKER[@]}" compose version >/dev/null

    resolve_bundle
    resolve_services "$BUNDLE_DIR"
    local active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    local active_env="$active_dir/.env"
    printf 'Deploy scope: %s\n' "${SERVICES[*]}"

    if [[ ! -f "$active_env" ]]; then
        if [[ -f "$BUNDLE_DIR/.env" ]]; then
            cp "$BUNDLE_DIR/.env" "$active_env"
        elif [[ -f "$active_dir/.env.example" ]]; then
            cp "$active_dir/.env.example" "$active_env"
        else
            echo "No .env found in DATA_DOWNLOADER to use." >&2
            exit 1
        fi
        set_env_value "$active_env" MARKET_DATA_PATH "$LOCAL_MARKET_DATA"
        set_env_value "$active_env" ARCHIVE_DATA_PATH "$LOCAL_ARCHIVE_DATA"
        set_env_value "$active_env" RELEASE_IMAGE_PATH "$active_dir/images"
    fi

    # Never disrupt a running local capture — unless forced. A frontend-only deploy is
    # exempt: it is recreated with --no-deps, so the backend container (and therefore
    # capture) is never touched, which makes it safe inside market hours.
    local touches_backend=false
    [[ " ${SERVICES[*]} " == *" backend "* ]] && touches_backend=true
    local market_open market_close tz
    market_open="$(env_value "$active_env" MARKET_OPEN)"; [[ -n "$market_open" ]] || market_open=09:00
    market_close="$(env_value "$active_env" MARKET_CLOSE)"; [[ -n "$market_close" ]] || market_close=15:30
    tz="$(env_value "$active_env" TIMEZONE)"; [[ -n "$tz" ]] || tz=Asia/Kolkata
    if [[ "$touches_backend" == false ]]; then
        printf 'Frontend-only deploy: capture is untouched, skipping the market-window guard.\n'
    elif [[ "$FORCE" == true ]]; then
        printf 'FORCE: bypassing market-window and capture-stopped guards (local).\n' >&2
    else
        assert_outside_capture_window "$market_open" "$market_close" "$tz"
        assert_capture_stopped "$active_env" false
    fi

    mkdir -p "$(env_value "$active_env" MARKET_DATA_PATH)" "$(env_value "$active_env" ARCHIVE_DATA_PATH)"

    copy_active_bundle_to_rollback

    printf 'Staging bundle into DATA_DOWNLOADER...\n'
    # Only a full-stack deploy takes the stack down first; a partial one recreates just
    # its own service(s) below so the others keep serving.
    if [[ -f "$active_dir/docker-compose.yml" && ${#SERVICES[@]} -eq 2 ]]; then
        printf 'Stopping existing local stack...\n'
        (cd "$active_dir" && "${DOCKER[@]}" compose down)
    fi

    mkdir -p "$active_dir/images"
    cp "$BUNDLE_DIR/docker-compose.yml" "$active_dir/docker-compose.yml"
    cp "$BUNDLE_DIR/version.json" "$active_dir/version.json"
    cp "$BUNDLE_DIR/manifest.json" "$active_dir/manifest.json"
    local service
    for service in "${SERVICES[@]}"; do
        cp "$BUNDLE_DIR/images/${service}.tar.gz" "$active_dir/images/${service}.tar.gz"
    done

    local release_id previous_version
    release_id="$(jq -r '.version' "$BUNDLE_DIR/version.json")"
    previous_version="$(env_value "$active_env" APP_VERSION)"

    printf 'Loading images...\n'
    for service in "${SERVICES[@]}"; do
        "${DOCKER[@]}" load -i "$active_dir/images/${service}.tar.gz"
    done

    # compose.yaml tags BOTH services with ${APP_VERSION}, so a service we are not
    # shipping needs its currently-running image re-tagged to the new release id —
    # otherwise a later plain `compose up` would look for an image that never existed.
    retag_skipped_services "$release_id" "$previous_version"

    set_env_value "$active_env" APP_VERSION "$release_id"

    printf 'Composing up (%s) in DATA_DOWNLOADER...\n' "${SERVICES[*]}"
    local -a up_args=(up -d)
    [[ "$FORCE" == true ]] && up_args+=(--force-recreate --remove-orphans)
    if [[ ${#SERVICES[@]} -lt 2 ]]; then
        # --no-deps is what keeps a frontend-only deploy from restarting the backend.
        up_args+=(--no-deps "${SERVICES[@]}")
    fi
    (cd "$active_dir" && "${DOCKER[@]}" compose "${up_args[@]}")

    health_check_stack "$active_env" "$active_env" "${SERVICES[*]}" || {
        echo "Local stack failed health checks." >&2; exit 1;
    }
    printf 'Local stack is up and healthy (%s).\n' "${SERVICES[*]}"
}

# Re-tag the images of services this deploy does NOT ship, from the previously active
# version to the new release id, so ${APP_VERSION} resolves for every service in the
# compose file. Best-effort: a missing previous image is reported, not fatal, because
# the service may simply never have been deployed here.
retag_skipped_services() {
    local release_id=$1 previous_version=$2 svc image
    [[ -n "$previous_version" && "$previous_version" != "$release_id" ]] || return 0
    for svc in backend frontend; do
        [[ " ${SERVICES[*]} " == *" $svc "* ]] && continue
        image="market-data-dwndr-${svc}"
        if "${DOCKER[@]}" image inspect "${image}:${previous_version}" >/dev/null 2>&1; then
            "${DOCKER[@]}" tag "${image}:${previous_version}" "${image}:${release_id}"
            printf 'Re-tagged unchanged %s image %s -> %s\n' "$svc" "$previous_version" "$release_id"
        else
            printf 'Note: no %s:%s image to re-tag; %s is not part of this release.\n' \
                "$image" "$previous_version" "$svc" >&2
        fi
    done
}

ship_bundle() {
    local bundle_dir=$1 ssh_user ssh_host deploy_dir remote
    for cmd in jq sha256sum rsync ssh; do
        command -v "$cmd" >/dev/null || { printf '%s is required.\n' "$cmd" >&2; exit 1; }
    done
    [[ -f "$SHIP_KEY" ]] || { echo "SSH key is missing: $SHIP_KEY" >&2; exit 1; }
    verify_bundle_sha256 "$bundle_dir"
    ssh_user="$(release_config VPS_SSH_USER)"
    ssh_host="$(release_config VPS_SSH_HOST)"
    deploy_dir="$(release_config VPS_DEPLOY_DIR)"
    [[ "$ssh_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { echo "VPS_SSH_USER is missing/invalid." >&2; exit 1; }
    [[ -n "$ssh_host" ]] || { echo "VPS_SSH_HOST is missing." >&2; exit 1; }
    [[ "$deploy_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$deploy_dir" != *".."* ]] || {
        echo "VPS_DEPLOY_DIR must be a safe absolute path." >&2; exit 1;
    }
    remote="${ssh_user}@${ssh_host}"
    local ssh_cmd=(ssh -i "$SHIP_KEY" -o IdentitiesOnly=yes)

    printf 'Ensuring remote deploy dir %s...\n' "$deploy_dir"
    "${ssh_cmd[@]}" "$remote" "mkdir -p $(printf '%q' "$deploy_dir")"

    printf 'Syncing bundle to %s:%s (preserving remote .env and MARKET_DATA)...\n' "$remote" "$deploy_dir"
    # -v lists each file as it is sent (current + already-synced); --info=progress2
    # shows a single overall progress bar with percent, rate and ETA; --partial keeps
    # partial files so a dropped transfer of the large image tarballs resumes.
    rsync -azh --delete --partial --info=progress2 -v \
        --exclude='.env' --exclude='MARKET_DATA' --exclude='ROLLBACKS' --exclude='ARCHIVE' \
        -e "ssh -i $(printf '%q' "$SHIP_KEY") -o IdentitiesOnly=yes" \
        "$bundle_dir/" "$remote:$deploy_dir/"

    printf 'Running the remote deploy...\n'
    local remote_flags=""
    [[ "$FORCE" == true ]] && remote_flags+=" --force"
    # Forward the scope so the VPS recreates only what we shipped. Without this a
    # frontend-only release would still restart the backend there.
    if [[ ${#SERVICES[@]} -lt 2 ]]; then
        local svc
        for svc in "${SERVICES[@]}"; do remote_flags+=" --$svc"; done
    fi
    "${ssh_cmd[@]}" "$remote" \
        "cd $(printf '%q' "$deploy_dir") && chmod +x deploy.sh rollback.sh && ./deploy.sh${remote_flags}"
    printf 'Shipped and deployed %s (%s) on %s.\n' \
        "$(release_bundle_version "$bundle_dir")" "${SERVICES[*]}" "$remote"
}

acquire_release_lock "$(global_release_lock_file)"
if [[ "$FORCE" == true && -n "$SHIP_KEY" ]]; then
    # Force update: force-deploy locally first, then ship + force-deploy on the VPS.
    deploy_local
    active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    [[ -f "$active_dir/manifest.json" ]] || { echo "Nothing staged in DATA_DOWNLOADER to ship. Run export first." >&2; exit 1; }
    ship_bundle "$active_dir"
elif [[ -n "$SHIP_KEY" ]]; then
    active_dir="$RELEASE_DIR/DATA_DOWNLOADER"
    [[ -f "$active_dir/manifest.json" ]] || { echo "Nothing staged in DATA_DOWNLOADER to ship. Run local deploy first." >&2; exit 1; }
    # Ship-only: scope comes from what was staged locally, narrowed by any flag given.
    resolve_services "$active_dir"
    ship_bundle "$active_dir"
else
    deploy_local
fi
