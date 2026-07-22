#!/bin/sh
set -eu

PROJECT_DIR=${PROJECT_DIR:-/srv/dev_stack/market_data_dwndr}
BACKEND_ENV=${BACKEND_ENV:-"${PROJECT_DIR}/backend/.env"}

read_env_value() {
    variable_name=$1
    value=$(sed -n "s/^${variable_name}=//p" "${BACKEND_ENV}" | tail -n 1 | tr -d '\r')
    value=${value#\"}
    value=${value%\"}
    if [ -z "${value}" ]; then
        echo "Missing ${variable_name} in ${BACKEND_ENV}" >&2
        exit 1
    fi
    printf '%s\n' "${value}"
}

LIVE_DATA_PATH=$(read_env_value MARKET_DATA_PATH)
ARCHIVE_DATA_PATH=$(read_env_value ARCHIVE_DATA_PATH)
APP_UID=$(read_env_value APP_UID)
HTTP_HOST=$(read_env_value HTTP_HOST)
HOST_BIND_ADDRESS=$(read_env_value HOST_BIND_ADDRESS)

if [ "${HTTP_HOST}" != "0.0.0.0" ]; then
    echo "Docker requires HTTP_HOST=0.0.0.0 inside the backend container." >&2
    exit 1
fi

case "${HOST_BIND_ADDRESS}" in
    127.0.0.1) ;;
    100.*)
        command -v tailscale >/dev/null || {
            echo "tailscale is required for a 100.x HOST_BIND_ADDRESS." >&2
            exit 1
        }
        tailscale_ip=$(tailscale ip -4 2>/dev/null | head -n 1)
        if [ "${HOST_BIND_ADDRESS}" != "${tailscale_ip}" ]; then
            echo "HOST_BIND_ADDRESS does not match this host's Tailscale IPv4 address." >&2
            exit 1
        fi
        ;;
    *)
        echo "HOST_BIND_ADDRESS must be loopback or the home VPS Tailscale address." >&2
        exit 1
        ;;
esac

for required_path in "${PROJECT_DIR}" "${LIVE_DATA_PATH}" "${ARCHIVE_DATA_PATH}"; do
    if [ ! -d "${required_path}" ]; then
        echo "Required directory is missing: ${required_path}" >&2
        exit 1
    fi
done

archive_mount=$(findmnt -n -o TARGET -T "${ARCHIVE_DATA_PATH}" 2>/dev/null || true)
if [ "${archive_mount}" != "/srv/data" ]; then
    echo "Archive path is not on the expected /srv/data mount: ${ARCHIVE_DATA_PATH}" >&2
    exit 1
fi

live_mount=$(findmnt -n -o TARGET -T "${LIVE_DATA_PATH}" 2>/dev/null || true)
if [ "${live_mount}" != "/srv/dev_stack" ]; then
    echo "Live path is not on the expected /srv/dev_stack SSD mount: ${LIVE_DATA_PATH}" >&2
    exit 1
fi

project_real=$(realpath -m "${PROJECT_DIR}")
live_real=$(realpath -m "${LIVE_DATA_PATH}")
archive_real=$(realpath -m "${ARCHIVE_DATA_PATH}")
case "${live_real}" in
    "${project_real}"/*) ;;
    *)
        echo "MARKET_DATA_PATH must be inside ${project_real}." >&2
        exit 1
        ;;
esac
if [ "${archive_real}" != "/srv/data/z_market_data" ]; then
    echo "ARCHIVE_DATA_PATH must resolve to /srv/data/z_market_data." >&2
    exit 1
fi

for writable_path in "${LIVE_DATA_PATH}" "${ARCHIVE_DATA_PATH}"; do
    owner_uid=$(stat -c '%u' "${writable_path}")
    owner_mode=$(stat -c '%A' "${writable_path}")
    if [ "${owner_uid}" != "${APP_UID}" ] || [ "$(printf '%s' "${owner_mode}" | cut -c3)" != "w" ]; then
        echo "${writable_path} must be owned and owner-writable by APP_UID ${APP_UID}." >&2
        exit 1
    fi
done

echo "Preflight passed: live data is on SSD and archives are on /srv/data."
