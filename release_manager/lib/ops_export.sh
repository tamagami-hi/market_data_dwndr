#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ops_export.sh — BUILD stage. Runs ONLY on this computer, never on the VPS.
#
# Run from status.sh (Export menu, or --do export). Responsibilities:
#   1. Determine the version label — the only place that does. It reads
#      __version__ and never writes it; only a release moves the version.
#   2. Build the backend and frontend images from the working tree and prove
#      each one starts, answers its own healthcheck, and runs as non-root.
#   3. docker save | gzip them into a versioned bundle under build/<host>/.
#   4. Copy the VPS-native scripts, compose file, paths.json, .env.example and
#      the guide into the bundle, so it is everything the VPS needs.
#   5. Write manifest.json and checksums.sha256.
#
# Explicitly NOT this module's job: touching the VPS, running the stack here,
# or any git write. There is no local deploy step: the acceptance run proves
# the images start, and the VPS deploy gates on their health.
# ─────────────────────────────────────────────────────────────────────────────

BACKEND_BUILD_ENV=backend/.env
FRONTEND_BUILD_ENV=frontend/.env.local
# Variables the VPS .env may hold that the backend's template does not: the
# frontend's runtime port.
VPS_ONLY_ENV_KEYS='PORT'

# op_export <paths.json> <stack> [--skip-build] [--keep N] [--backend-url URL]
op_export() {
    local paths_file="$1" stack="$2"; shift 2
    local skip_build=false keep=3 backend_url=''
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-build) skip_build=true; shift ;;
            --keep)
                [[ "${2:-}" =~ ^[0-9]+$ && "$2" -ge 1 ]] || { err "--keep needs a positive integer"; exit 1; }
                keep="$2"; shift 2 ;;
            # The default build is same-origin (an empty NEXT_PUBLIC_BACKEND_URL:
            # relative /api and a window.location WebSocket). --backend-url bakes
            # an absolute backend origin into the frontend instead.
            --backend-url)
                [[ "${2:-}" =~ ^https?://[A-Za-z0-9.:-]+$ ]] || { err "--backend-url needs an http(s) origin"; exit 1; }
                backend_url="$2"; shift 2 ;;
            *) err "unknown export option: $1"; exit 1 ;;
        esac
    done

    local c
    for c in docker jq gzip sha256sum git; do
        command -v "$c" >/dev/null || { err "$c is required"; exit 1; }
    done
    docker compose version >/dev/null 2>&1 || { err "docker compose is required"; exit 1; }

    # ── version label ───────────────────────────────────────────────────────
    local label version kind
    label="$(build_label "$ROOT_DIR")" || exit 1
    version="${label% *}"; kind="${label##* }"

    local stamp build_name bundle git_sha git_branch git_dirty=false
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    build_name="$(host_build_dir "$stack")" || exit 1
    bundle="$BUILD_DIR/$build_name/${version}-${stamp}"
    git_sha="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    git_branch="$(git -C "$ROOT_DIR" symbolic-ref --short -q HEAD 2>/dev/null || echo detached)"
    git_dirty "$ROOT_DIR" && git_dirty=true

    banner "EXPORT · $stack"
    field "environment" "$(stack_attr "$stack" env)"
    field "version"     "$version"
    field "kind"        "$kind"
    field "commit"      "${git_sha:0:9}$([[ "$git_dirty" == true ]] && printf ' (dirty)')"
    field "frontend"    "${backend_url:-same-origin}"
    field "bundle"      "${bundle#"$ROOT_DIR"/}"

    mkdir -p "$bundle/images"
    # A bundle is only meaningful once manifest.json exists. If this aborts
    # before that, the partial directory goes, so the newest-bundle selection
    # can never pick up a half-built release.
    EXPORT_BUNDLE="$bundle"; EXPORT_COMPLETE=false
    trap 'if [[ "$EXPORT_COMPLETE" != true && -d "$EXPORT_BUNDLE" ]]; then rm -rf -- "$EXPORT_BUNDLE"; printf "   removed incomplete bundle\n" >&2; fi' EXIT

    # ── build ───────────────────────────────────────────────────────────────
    local key archive tag
    if [[ "$skip_build" == true ]]; then
        section "BUILD IMAGES" "skipped by --skip-build"
        while IFS=: read -r key archive; do
            tag="$(stack_image_tag "$stack" "$key" "$version")"
            docker image inspect "$tag" >/dev/null 2>&1 \
                || { err "image not present: $tag (build it first)"; exit 1; }
        done < <(stack_images "$stack")
    else
        section "BUILD IMAGES" "$(stack_image_tag "$stack" '{backend,frontend}' "$version")"
        export_assert_build_inputs
        # A shell variable beats the --env-file value, so this sets the label
        # and the backend URL without touching the developer's env files.
        NEXT_PUBLIC_BACKEND_URL="$backend_url" APP_VERSION="$version" docker compose \
            --project-directory "$ROOT_DIR" -f "$ROOT_DIR/compose.yaml" \
            --env-file "$ROOT_DIR/$BACKEND_BUILD_ENV" --env-file "$ROOT_DIR/$FRONTEND_BUILD_ENV" \
            build --pull || { err "the image build failed"; exit 1; }
        ok "images built"
    fi

    export_accept_backend "$(stack_image_tag "$stack" backend "$version")"
    export_accept_frontend "$(stack_image_tag "$stack" frontend "$version")"

    # ── save ────────────────────────────────────────────────────────────────
    section "SAVE IMAGES"
    local images_json='{}' sha
    local -a compress=(gzip -n -9)
    command -v pigz >/dev/null 2>&1 && compress=(pigz -n -9)
    while IFS=: read -r key archive; do
        [[ -n "$key" ]] || continue
        tag="$(stack_image_tag "$stack" "$key" "$version")"
        step "saving $tag"
        # -n omits the timestamp so identical input yields an identical
        # archive, and the checksum means something across rebuilds.
        docker save "$tag" | "${compress[@]}" > "$bundle/images/$archive" \
            || { err "failed to save $tag"; exit 1; }
        sha="$(sha256sum "$bundle/images/$archive" | cut -d' ' -f1)"
        images_json="$(jq --arg k "$key" --arg tag "$tag" --arg a "images/$archive" --arg s "$sha" \
            --arg id "$(docker image inspect --format '{{.Id}}' "$tag")" \
            '.[$k] = {tag: $tag, archive: $a, sha256: $s, image_id: $id}' <<<"$images_json")"
        ok "$archive  $(du -h "$bundle/images/$archive" | cut -f1)"
    done < <(stack_images "$stack")

    # ── stage ───────────────────────────────────────────────────────────────
    section "STAGE VPS ARTIFACTS"
    export_stage_files "$stack" "$paths_file" "$bundle"

    # ── manifest ────────────────────────────────────────────────────────────
    section "MANIFEST"
    local compose_name
    compose_name="$(stack_attr "$stack" compose)"
    jq -n \
        --arg version "$version" --arg kind "$kind" --arg stack "$stack" \
        --arg host "$DD_HOST" --arg environment "$(stack_attr "$stack" env)" \
        --arg created_at "$stamp" --arg git_sha "$git_sha" --arg git_branch "$git_branch" \
        --argjson git_dirty "$git_dirty" \
        --arg backend_url "$backend_url" --arg build_hash "$(export_build_hash "$backend_url")" \
        --arg compose_file "$compose_name" \
        --arg compose_sha "$(sha256sum "$bundle/$compose_name" | cut -d' ' -f1)" \
        --arg paths_sha "$(sha256sum "$bundle/paths.json" | cut -d' ' -f1)" \
        --argjson images "$images_json" \
        '{version: $version, kind: $kind, stack: $stack, host: $host,
          environment: $environment, created_at: $created_at,
          git_sha: $git_sha, git_branch: $git_branch, git_dirty: $git_dirty,
          build: {backend_url: (if $backend_url == "" then "same-origin" else $backend_url end),
                  config_hash: $build_hash},
          compose: {file: $compose_file, sha256: $compose_sha},
          paths: {file: "paths.json", sha256: $paths_sha},
          images: $images}' > "$bundle/manifest.json"
    jq empty "$bundle/manifest.json" || { err "generated an invalid manifest"; exit 1; }

    # A flat list too, so sha256sum -c works on the VPS. Every path resolves
    # relative to the stack directory, where both verifiers run.
    ( cd "$bundle" && find . -type f ! -name 'checksums.sha256' -print0 \
        | sort -z | xargs -0 -r sha256sum > checksums.sha256 )
    EXPORT_COMPLETE=true
    ok "manifest.json and checksums.sha256 written"

    # ── retention ───────────────────────────────────────────────────────────
    local -a old
    local v
    mapfile -t old < <(bundle_dirs_oldest_first "$BUILD_DIR/$build_name")
    if (( ${#old[@]} > keep )); then
        for v in "${old[@]:0:$(( ${#old[@]} - keep ))}"; do
            rm -rf -- "${BUILD_DIR:?}/$build_name/$v" && info "pruned old bundle $v"
        done
    fi

    banner "BUNDLE READY"
    field "version" "$version"
    field "path"    "${bundle#"$ROOT_DIR"/}"
    field "size"    "$(du -sh "$bundle" | cut -f1)"
}

# The cheap mistakes that build fine and ship broken.
export_assert_build_inputs() {
    local f
    for f in compose.yaml backend/Dockerfile frontend/Dockerfile "$BACKEND_BUILD_ENV" "$FRONTEND_BUILD_ENV"; do
        [[ -f "$ROOT_DIR/$f" ]] || { err "missing build input: $f"; exit 1; }
    done
    local key
    for key in APP_UID APP_GID; do
        [[ "$(sed -n "s/^$key=//p" "$ROOT_DIR/$BACKEND_BUILD_ENV" | tail -n1)" =~ ^[0-9]+$ ]] \
            || { err "$key in $BACKEND_BUILD_ENV must be numeric; the images run as it"; exit 1; }
    done
}

# export_build_hash <backend_url> — the build settings baked into the images,
# so two bundles of one commit built differently are told apart.
export_build_hash() {
    local app_name uid gid
    app_name="$(sed -n 's/^NEXT_PUBLIC_APP_NAME=//p' "$ROOT_DIR/$FRONTEND_BUILD_ENV" 2>/dev/null | tail -n1)"
    uid="$(sed -n 's/^APP_UID=//p' "$ROOT_DIR/$BACKEND_BUILD_ENV" 2>/dev/null | tail -n1)"
    gid="$(sed -n 's/^APP_GID=//p' "$ROOT_DIR/$BACKEND_BUILD_ENV" 2>/dev/null | tail -n1)"
    printf 'NEXT_PUBLIC_BACKEND_URL=%s\nNEXT_PUBLIC_APP_NAME=%s\nAPP_UID=%s\nAPP_GID=%s\n' \
        "$1" "${app_name:-TickVault}" "$uid" "$gid" | sha256sum | cut -c1-12
}

# export_accept_container <name> <probe...> — poll a probe inside a running
# container until it passes, the container dies, or 60 s go by.
export_accept_container() {
    local name="$1"; shift
    local waited=0 state
    while (( waited < 60 )); do
        state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null)" || state=missing
        [[ "$state" == running ]] || return 1
        docker exec "$name" "$@" >/dev/null 2>&1 && return 0
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

export_assert_non_root() {
    local tag="$1" uid
    uid="$(docker run --rm --network none --entrypoint id "$tag" -u 2>/dev/null || echo 0)"
    [[ "$uid" =~ ^[0-9]+$ && "$uid" != 0 ]] || { err "$tag runs as root"; exit 1; }
    ok "runs as uid $uid"
}

# Prove the backend starts and answers /health. It runs with throwaway settings
# and no network, read-only as on the VPS: no broker is reached and no data is
# written outside its own tmpfs.
export_accept_backend() {
    local tag="$1" name="dd-accept-backend-$$" out
    section "RUNTIME ACCEPTANCE · backend"
    step "starts on throwaway settings, with no network, and answers /health"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --network none --read-only \
        --tmpfs /tmp:rw,nosuid,size=64m --tmpfs /accept:rw,nosuid,size=64m \
        -e KITE_API_KEY=acceptance -e KITE_API_SECRET=acceptance \
        -e MARKET_DATA_PATH=/accept/market_data -e ARCHIVE_DATA_PATH=/accept/archive \
        -e HTTP_HOST=127.0.0.1 -e HTTP_PORT=9000 -e FRONTEND_URL=http://localhost:3000 \
        "$tag" >/dev/null 2>&1 || { err "the backend image would not start at all"; exit 1; }
    if ! export_accept_container "$name" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=3)"; then
        out="$(docker logs --tail 20 "$name" 2>&1 || true)"
        docker rm -f "$name" >/dev/null 2>&1 || true
        err "the backend did not answer /health:"; printf '%s\n' "$out" >&2; exit 1
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    ok "backend answers /health"
    export_assert_non_root "$tag"
}

# Prove the frontend starts and serves /login, as its healthcheck does.
export_accept_frontend() {
    local tag="$1" name="dd-accept-frontend-$$" out
    section "RUNTIME ACCEPTANCE · frontend"
    step "starts with no network and serves /login"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --network none --read-only \
        --tmpfs /tmp:rw,nosuid,size=64m --tmpfs /app/.next/cache:rw,nosuid,size=64m \
        -e PORT=3000 -e HOSTNAME=0.0.0.0 -e NODE_ENV=production \
        "$tag" >/dev/null 2>&1 || { err "the frontend image would not start at all"; exit 1; }
    if ! export_accept_container "$name" node -e \
        "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"; then
        out="$(docker logs --tail 20 "$name" 2>&1 || true)"
        docker rm -f "$name" >/dev/null 2>&1 || true
        err "the frontend did not serve /login:"; printf '%s\n' "$out" >&2; exit 1
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    ok "frontend serves /login"
    export_assert_non_root "$tag"
}

export_stage_files() {
    local stack="$1" paths_file="$2" bundle="$3"
    local src="$RM_DIR/stacks/$stack"
    local compose_name deploy_name rollback_name guide_name
    compose_name="$(stack_attr "$stack" compose)"
    deploy_name="$(stack_attr "$stack" deploy)"
    rollback_name="$(stack_attr "$stack" rollback)"
    guide_name="$(stack_attr "$stack" guide)"

    export_assert_env_keys "$src/.env.example"
    grep -q 'DD_VERSION' "$src/$compose_name" \
        || { err "$compose_name does not take its image tags from DD_VERSION"; exit 1; }

    cp "$src/$compose_name"  "$bundle/$compose_name"
    cp "$src/$deploy_name"   "$bundle/$deploy_name"
    cp "$src/$rollback_name" "$bundle/$rollback_name"
    cp "$src/.env.example"   "$bundle/.env.example"
    [[ -f "$src/$guide_name" ]] && cp "$src/$guide_name" "$bundle/$guide_name"
    # The shared runtime travels with every bundle, so the stack directory on
    # the VPS is self-sufficient over nothing but SSH.
    cp "$RM_DIR/stacks/_shared/_dd_lib.sh" "$RM_DIR/stacks/_shared/_dd_deploy.sh" \
       "$RM_DIR/stacks/_shared/_dd_rollback.sh" "$bundle/"
    chmod +x "$bundle/$deploy_name" "$bundle/$rollback_name"

    # paths.json is copied byte for byte — never generated here.
    cp "$paths_file" "$bundle/paths.json"
    ok "staged compose, scripts, .env.example, the guide and paths.json"
}

# Every variable the VPS .env.example declares must be one the backend reads
# (backend/.env.example) or one of the few VPS-only ones. A template that names
# a variable nothing reads (a stale deploy path, say) is worse than none.
# Compared on names only: values differ on purpose.
export_assert_env_keys() {
    local stack_example="$1" app_example="$ROOT_DIR/backend/.env.example" stale
    [[ -f "$app_example" ]] || { warn "no backend/.env.example to compare against"; return 0; }
    env_keys() { grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | tr -d '=' | sort -u; }
    stale="$(comm -23 <(env_keys "$stack_example") \
        <({ env_keys "$app_example"; tr ' ' '\n' <<<"$VPS_ONLY_ENV_KEYS"; } | sort -u))"
    if [[ -n "$stale" ]]; then
        err "the stack .env.example declares variables nothing reads:"
        sed 's/^/     /' <<<"$stale" >&2
        exit 1
    fi
    ok ".env.example declares only variables the stack reads ($(env_keys "$stack_example" | wc -l))"
}
