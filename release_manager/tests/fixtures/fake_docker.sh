#!/usr/bin/env bash

set -euo pipefail

case "${1:-} ${2:-} ${3:-}" in
    'info  ') exit 0 ;;
    compose*) exit 0 ;;
    'image inspect --format')
        tag=${5:-}
        if [[ "$tag" == market-data-dwndr-backend:* ]]; then
            printf 'sha256:%064d\n' 1
        else
            printf 'sha256:%064d\n' 2
        fi
        ;;
    'image inspect '*) exit 0 ;;
    'image tag '*) exit 0 ;;
    'image rm '*) exit 0 ;;
    'image save '*)
        tag=${3:-unknown}
        fake_dir=$(mktemp -d)
        trap 'rm -rf "$fake_dir"' EXIT
        printf '[{"Config":"config.json","RepoTags":["%s"],"Layers":[]}]\n' "$tag" \
            > "$fake_dir/manifest.json"
        tar -cf - -C "$fake_dir" manifest.json
        ;;
    *)
        printf 'unexpected fake docker invocation:' >&2
        printf ' %q' "$@" >&2
        printf '\n' >&2
        exit 1
        ;;
esac
