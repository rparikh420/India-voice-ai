#!/usr/bin/env bash
# OpenClaw bootstrap for macOS — guided install + hardening.
#
# This is deliberately interactive and stops at each gate. It does NOT blindly
# overwrite an existing config. Run it from the repo root:
#
#   bash ./bootstrap.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_HOME="${HOME}/.openclaw"
WORKSPACE="${OPENCLAW_HOME}/workspace"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
gate() {
  printf '\n\033[1m%s\033[0m\n' "$1"
  read -r -p "  Continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "  Stopped."; exit 0; }
}

# ── 1. Prerequisites ────────────────────────────────────────────────────────
bold "1. Checking prerequisites"

if ! command -v node >/dev/null 2>&1; then
  warn "Node not found. Install Node 24 first:  brew install node@24"
  exit 1
fi

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
info "node $(node --version)"
if (( NODE_MAJOR < 22 )); then
  warn "OpenClaw needs Node 22.22.3+, 24.15+ (recommended), or 25.9+."
  exit 1
fi
if (( NODE_MAJOR == 23 )); then
  warn "Node 23 is not a supported line. Prefer Node 24."
fi

if ! command -v docker >/dev/null 2>&1; then
  warn "Docker not found — the sandboxed 'dev' agent needs it."
  warn "Either install Docker Desktop, or set agents.entries.dev.sandbox.mode='off'"
  warn "and gate exec behind approvals instead. See README Phase 7.1."
fi

# ── 2. Install ──────────────────────────────────────────────────────────────
if command -v openclaw >/dev/null 2>&1; then
  bold "2. OpenClaw already installed ($(openclaw --version 2>/dev/null || echo unknown))"
  gate "Update to latest?"
  npm install -g openclaw@latest
else
  gate "2. Install OpenClaw globally via npm?"
  npm install -g openclaw@latest
fi

# ── 3. Onboard + daemon ─────────────────────────────────────────────────────
gate "3. Run the onboarding wizard and install the launchd daemon?
   You'll pick a provider and authenticate. Choose OpenRouter.
   Phase 2 of the README replaces the model config afterwards."
openclaw onboard --install-daemon

# ── 4. Secrets ──────────────────────────────────────────────────────────────
bold "4. Secrets"
info "These must be present in the daemon's environment, not just your shell."
info "Set them, then restart the gateway:"
cat <<'EOF'

    openclaw config set env.OPENROUTER_API_KEY  "sk-or-..."
    openclaw config set env.DISCORD_BOT_TOKEN   "..."
    openclaw config set env.GITHUB_TOKEN        "ghp_..."

EOF
warn "Never commit these. They live in ~/.openclaw/openclaw.json (mode 600)."
gate "Set your secrets in another terminal now, then continue."

# ── 5. Config ───────────────────────────────────────────────────────────────
bold "5. Configuration"
CONFIG_PATH="${OPENCLAW_HOME}/openclaw.json"
if [[ -f "$CONFIG_PATH" ]]; then
  BACKUP="${CONFIG_PATH}.bak.$(date +%Y%m%d%H%M%S)"
  info "Existing config found. Backing up to:"
  info "  ${BACKUP}"
  cp "$CONFIG_PATH" "$BACKUP"
  warn "NOT overwriting automatically — onboarding wrote real values in there."
  warn "Merge ${SCRIPT_DIR}/openclaw.config.json5 section by section, replacing"
  warn "every ALL_CAPS placeholder (server ID, user ID, channel IDs, timezone)."
else
  info "No existing config; copying the starter."
  mkdir -p "$OPENCLAW_HOME"
  cp "${SCRIPT_DIR}/openclaw.config.json5" "$CONFIG_PATH"
  warn "Now edit ${CONFIG_PATH} and replace every ALL_CAPS placeholder."
fi

# ── 6. Workspace identity files ─────────────────────────────────────────────
gate "6. Install workspace identity files (SOUL/IDENTITY/USER/AGENTS)?
   Existing files will NOT be overwritten."
mkdir -p "$WORKSPACE"
for f in "${SCRIPT_DIR}"/workspace/*.md; do
  base="$(basename "$f")"
  if [[ -e "${WORKSPACE}/${base}" ]]; then
    info "skip  ${base} (already exists)"
  else
    cp "$f" "${WORKSPACE}/${base}"
    info "copy  ${base}"
  fi
done
warn "Open ${WORKSPACE}/USER.md and fill it in properly. This is the single"
warn "highest-leverage hour in the whole setup."

# ── 7. Harden ───────────────────────────────────────────────────────────────
bold "7. Hardening"
gate "Run the security audit and apply safe fixes (tightens perms to 600/700)?"
openclaw security audit || true
openclaw security audit --fix || true

# ── 8. Sleep behaviour ──────────────────────────────────────────────────────
bold "8. Always-on behaviour (laptop caveat)"
cat <<'EOF'
  Cron runs INSIDE the gateway process. While the Mac sleeps, scheduled jobs
  do not fire. To reduce missed runs:

    System Settings > Battery > Options
      - Prevent automatic sleeping on power adapter:  ON
      - Wake for network access:                      ON

    # Wake daily at 06:45 so the morning brief fires:
    sudo pmset repeat wakeorpoweron MTWRFSU 06:45:00

  The starter cron jobs are written to be idempotent and to summarise what was
  missed, rather than assuming they fired exactly on schedule.
EOF

# ── 9. Verify ───────────────────────────────────────────────────────────────
bold "9. Verifying"
openclaw gateway restart || true
openclaw gateway status || true

cat <<'EOF'

  Expected: gateway listening on 127.0.0.1:18789

  Next steps:
    openclaw dashboard              # Control UI, send a first message
    openclaw mcp doctor --probe     # after adding MCP servers (Phase 5)
    bash ./cron-jobs.sh      # install automations (Phase 6)

  Follow README.md from Phase 2 onward.

EOF
bold "Bootstrap complete."
