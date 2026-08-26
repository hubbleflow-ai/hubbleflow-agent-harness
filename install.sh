#!/usr/bin/env bash
# Install hubbleflow. Safe to re-run; upgrades in place.
set -euo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '  %s✗%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HUBBLEFLOW_HOME:-$HOME/.hubbleflow}"

# --dev installs editable, so `hubbleflow` runs the working tree rather than a
# copy of it. Without this a contributor edits src/ and sees nothing change.
DEV=""
for arg in "$@"; do
  case "$arg" in
    --dev) DEV="-e" ;;
    -h|--help) say "usage: ./install.sh [--dev]"; exit 0 ;;
    *) die "unknown option $arg" ;;
  esac
done

say ""
say "${BOLD}Installing hubbleflow${OFF}"
say ""

# ---- prerequisites -------------------------------------------------------
say "${DIM}checking prerequisites${OFF}"

case "$(uname -s)" in
  Darwin|Linux) ok "platform $(uname -s)" ;;
  *) die "unsupported platform $(uname -s). On Windows use install.ps1." ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found — installing it (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "couldn't install uv"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH; open a new shell and re-run"
fi
ok "uv $(uv --version | awk '{print $2}')"

# uv fetches its own Python, so a system Python 3.13 isn't required.
ok "python 3.13+ (uv will fetch it if needed)"

# ---- install -------------------------------------------------------------
say ""
say "${DIM}installing the command${OFF}"
# Output is captured rather than discarded: a failure here is the one moment
# uv's own message is worth more than ours.
if ! OUTPUT="$(uv tool install $DEV --from "$SOURCE_DIR" hubbleflow --force 2>&1)"; then
  printf '%s\n' "$OUTPUT" >&2
  die "install failed — the error above is from uv"
fi

BIN="$(command -v hubbleflow || true)"
[ -n "$BIN" ] || BIN="$HOME/.local/bin/hubbleflow"
[ -x "$BIN" ] || die "installed, but no hubbleflow binary found"
ok "hubbleflow $("$BIN" --version | awk '{print $2}') → $BIN"
[ -n "$DEV" ] && ok "editable — your working tree is what runs"

case ":$PATH:" in
  *":$(dirname "$BIN"):"*) ok "$(dirname "$BIN") is on PATH" ;;
  *) warn "$(dirname "$BIN") is not on PATH — add it to your shell profile:"
     say "      export PATH=\"\$PATH:$(dirname "$BIN")\"" ;;
esac

# ---- config --------------------------------------------------------------
say ""
say "${DIM}setting up $CONFIG_DIR${OFF}"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/.env" ]; then
  cat > "$CONFIG_DIR/.env" <<'ENVEOF'
# Fill in whichever providers you use. None are required to start —
# a local Ollama or Mesh model needs no key at all.
# GOOGLE_API_KEY=      # https://aistudio.google.com/apikey
# NVIDIA_API_KEY=      # https://build.nvidia.com
ENVEOF
  chmod 600 "$CONFIG_DIR/.env"
  ok "created $CONFIG_DIR/.env (keys go here)"
else
  ok "kept your existing $CONFIG_DIR/.env"
fi
# Skills are seeded from the package on first run, so nothing to copy here.

# ---- what next -----------------------------------------------------------
say ""
say "${BOLD}Done.${OFF} Run ${BOLD}hubbleflow${OFF} in any project directory."
say ""
if [ -z "${GOOGLE_API_KEY:-}${GEMINI_API_KEY:-}" ] && ! grep -q '^GOOGLE_API_KEY=.' "$CONFIG_DIR/.env" 2>/dev/null; then
  say "  No API key configured yet. Either:"
  say "    ${DIM}put a key in $CONFIG_DIR/.env${OFF}"
  say "    ${DIM}or run a local model — ollama serve, then: hubbleflow -m <model>${OFF}"
  say ""
fi
say "  ${DIM}hubbleflow --help      every flag${OFF}"
say "  ${DIM}/help inside           every command${OFF}"
say "  ${DIM}./uninstall.sh         remove it again${OFF}"
[ -z "$DEV" ] && say "  ${DIM}./install.sh --dev     reinstall editable, for working on hubbleflow itself${OFF}"
say ""
