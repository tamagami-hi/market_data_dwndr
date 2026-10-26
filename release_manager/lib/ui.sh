#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ui.sh — output helpers. Sourcing this has no side effects beyond defining
# functions and read-only colour constants.
# ─────────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
    c_rst=$'\033[0m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'
    c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_cyn=$'\033[36m'
    UI_INTERACTIVE=true
else
    c_rst=''; c_bold=''; c_dim=''; c_red=''; c_grn=''; c_ylw=''; c_cyn=''
    UI_INTERACTIVE=false
fi
[[ -t 0 ]] || UI_INTERACTIVE=false

banner()  { printf '\n%s══ %s ══%s\n\n' "$c_bold" "$1" "$c_rst"; }
section() { printf '\n%s%s%s%s\n' "$c_bold" "$1" "$c_rst" "${2:+  $c_dim$2$c_rst}"; }
step()    { printf '   %s→%s %s\n' "$c_cyn" "$c_rst" "$1"; }
ok()      { printf '   %s✓%s %s\n' "$c_grn" "$c_rst" "$1"; }
warn()    { printf '   %s!%s %s\n' "$c_ylw" "$c_rst" "$1" >&2; }
err()     { printf '   %s✗%s %s\n' "$c_red" "$c_rst" "$1" >&2; }
info()    { printf '     %s%s%s\n' "$c_dim" "$1" "$c_rst"; }
field()   { printf '     %-14s %s\n' "$1" "$2"; }

confirm() {
    local reply
    printf '   %s➜ %s [y/N] %s' "$c_bold" "$1" "$c_rst"
    read -r reply || reply=''
    [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]]
}
