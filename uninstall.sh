#!/usr/bin/env bash
# Remove hubbleflow. Your config and sessions are kept unless you ask otherwise.
set -euo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$*"; }

CONFIG_DIR="${HUBBLEFLOW_HOME:-$HOME/.hubbleflow}"
PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      printf 'usage: uninstall.sh [--purge]\n\n  --purge  also delete %s (config, sessions, history, skills)\n' "$CONFIG_DIR"
      exit 0 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

printf '\n%sRemoving hubbleflow%s\n\n' "$BOLD" "$OFF"

# Release any context caches before the tool goes away, so nothing keeps
# billing for storage after there's no way left to clean it up.
if command -v hubbleflow >/dev/null 2>&1; then
  hubbleflow --purge-caches >/dev/null 2>&1 && ok "released any active context caches" \
    || warn "couldn't reach the caching API (nothing may have been cached)"
fi

if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^hubbleflow'; then
  uv tool uninstall hubbleflow >/dev/null 2>&1 && ok "removed the hubbleflow command"
else
  warn "hubbleflow wasn't installed via uv — remove it however you installed it"
fi

if [ "$PURGE" -eq 1 ]; then
  if [ -d "$CONFIG_DIR" ]; then
    printf '\n  %sAbout to delete %s%s\n' "$YELLOW" "$CONFIG_DIR" "$OFF"
    printf '  This removes your API keys, saved sessions, shell history and skills.\n'
    printf '  Type %sdelete%s to confirm: ' "$BOLD" "$OFF"
    read -r reply
    if [ "$reply" = "delete" ]; then
      rm -rf "$CONFIG_DIR"
      ok "deleted $CONFIG_DIR"
    else
      warn "left $CONFIG_DIR alone"
    fi
  else
    ok "no $CONFIG_DIR to remove"
  fi
else
  ok "kept $CONFIG_DIR — re-run with --purge to delete it"
fi

printf '\n%sDone.%s\n\n' "$BOLD" "$OFF"
