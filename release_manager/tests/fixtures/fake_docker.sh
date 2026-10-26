#!/usr/bin/env bash
# fake_docker.sh — just enough of docker for the VPS-native scripts.
#
# State lives in $FAKE_STATE: images/<tag> (a file per image tag), containers/<name>
# (its running version), calls.log (every invocation). Knobs:
#   FAKE_CAPTURE      stopped | running | down   what /api/capture/status answers
#   FAKE_BAD_VERSION  a version whose containers turn unhealthy
#   FAKE_NO_LEASE     non-empty: the maintenance lease is refused
#   FAKE_MARKET_OPEN / FAKE_MARKET_CLOSE / FAKE_MARKET_TIMEZONE  the backend's env

set -euo pipefail
: "${FAKE_STATE:?FAKE_STATE not set}"
mkdir -p "$FAKE_STATE/images" "$FAKE_STATE/containers"
printf '%s\n' "$*" >> "$FAKE_STATE/calls.log"

tag_file() { printf '%s/images/%s' "$FAKE_STATE" "${1//\//_}"; }
running_version() { cat "$FAKE_STATE/containers/$1" 2>/dev/null || true; }

case "${1:-}" in
    info) exit 0 ;;
    inspect)
        format="$3"; name="$4"
        version="$(running_version "$name")"
        [[ -n "$version" ]] || exit 1
        case "$format" in
            *RestartCount*) printf '0\n' ;;
            *Health*)
                if [[ "$version" == "${FAKE_BAD_VERSION:-}" ]]; then printf 'unhealthy\n'; else printf 'healthy\n'; fi ;;
            *State.Status*) printf 'running\n' ;;
            *) printf '\n' ;;
        esac ;;
    exec)
        shift
        [[ "$1" == -i ]] && shift
        name="$1"; shift
        [[ -n "$(running_version "$name")" ]] || exit 1
        if [[ "$1" == printenv ]]; then
            case "$2" in
                MARKET_OPEN)     [[ -n "${FAKE_MARKET_OPEN:-}" ]] && printf '%s\n' "$FAKE_MARKET_OPEN" || exit 1 ;;
                MARKET_CLOSE)    [[ -n "${FAKE_MARKET_CLOSE:-}" ]] && printf '%s\n' "$FAKE_MARKET_CLOSE" || exit 1 ;;
                MARKET_TIMEZONE) [[ -n "${FAKE_MARKET_TIMEZONE:-}" ]] && printf '%s\n' "$FAKE_MARKET_TIMEZONE" || exit 1 ;;
                *) exit 1 ;;
            esac
            exit 0
        fi
        code="$(cat)"
        case "$code" in
            *capture/status*)
                [[ "${FAKE_CAPTURE:-stopped}" != down ]] || exit 1
                printf '%s\n' "${FAKE_CAPTURE:-stopped}" ;;
            *DELETE*) printf 'released %s\n' "${3:-}" >> "$FAKE_STATE/leases.log" ;;
            *maintenance*)
                [[ -z "${FAKE_NO_LEASE:-}" ]] || exit 1
                printf 'lease-0123456789abcdef\n'
                printf 'taken\n' >> "$FAKE_STATE/leases.log" ;;
            *) exit 1 ;;
        esac ;;
    image)
        case "$2" in
            inspect) [[ -f "$(tag_file "$3")" ]] ;;
            load)
                while IFS= read -r line; do
                    [[ "$line" == FAKEIMAGE\ * ]] && : > "$(tag_file "${line#FAKEIMAGE }")"
                done ;;
            ls) for f in "$FAKE_STATE"/images/*; do [[ -e "$f" ]] && basename "$f"; done; true ;;
            rm) rm -f "$(tag_file "$3")" ;;
            *) exit 1 ;;
        esac ;;
    save)
        [[ -f "$(tag_file "$2")" ]] || exit 1
        printf 'FAKEIMAGE %s\n' "$2" ;;
    logs) printf 'fake log line\n' ;;
    compose)
        shift
        [[ "$1" == version ]] && exit 0
        while [[ "$1" == --project-name || "$1" == --file ]]; do shift 2; done
        case "$1" in
            config) [[ -n "${DD_VERSION:-}" ]] ;;
            up)
                for svc in backend frontend; do
                    [[ -f "$(tag_file "market-data-dwndr-$svc:$DD_VERSION")" ]] \
                        || { printf 'no such image market-data-dwndr-%s:%s\n' "$svc" "$DD_VERSION" >&2; exit 1; }
                done
                printf '%s\n' "$DD_VERSION" > "$FAKE_STATE/containers/market-data-dwndr-backend"
                printf '%s\n' "$DD_VERSION" > "$FAKE_STATE/containers/market-data-dwndr-frontend" ;;
            down) rm -f "$FAKE_STATE"/containers/* ;;
            ps|logs) printf 'fake compose %s\n' "$1" ;;
            *) exit 1 ;;
        esac ;;
    *) printf 'unexpected fake docker call: %s\n' "$*" >&2; exit 1 ;;
esac
