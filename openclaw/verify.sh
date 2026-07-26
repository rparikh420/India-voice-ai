#!/usr/bin/env bash
# Verify an OpenClaw setup end to end.
#
#   bash openclaw/verify.sh
#
# Read-only: inspects state and reports. Never modifies config, never prints
# secret values (only whether they are set).
#
# Exit codes: 0 = all pass, 1 = at least one FAIL, 2 = warnings only.

set -uo pipefail

OPENCLAW_HOME="${OPENCLAW_HOME:-${HOME}/.openclaw}"
CONFIG_PATH="${OPENCLAW_HOME}/openclaw.json"
WORKSPACE="${OPENCLAW_HOME}/workspace"

PASS=0; WARN=0; FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
note() { printf '      %s\n' "$*"; }
sect() { printf '\n\033[1m%s\033[0m\n' "$*"; }

has() { command -v "$1" >/dev/null 2>&1; }

# ── Runtime ─────────────────────────────────────────────────────────────────
sect "Runtime"

if has node; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  NODE_FULL="$(node --version)"
  if (( NODE_MAJOR >= 24 )); then
    ok "node ${NODE_FULL}"
  elif (( NODE_MAJOR == 22 )); then
    ok "node ${NODE_FULL} (supported; 24.x recommended)"
  else
    bad "node ${NODE_FULL} — needs 22.22.3+, 24.15+, or 25.9+"
  fi
else
  bad "node not found"
fi

if has openclaw; then
  ok "openclaw $(openclaw --version 2>/dev/null || echo '(version unknown)')"
else
  bad "openclaw not on PATH"
  echo; echo "Nothing else to check. Run: bash openclaw/bootstrap.sh"; exit 1
fi

if has docker && docker info >/dev/null 2>&1; then
  ok "docker running (sandboxed dev agent available)"
else
  warn "docker not running — the sandboxed 'dev' agent needs it"
  note "either start Docker, or set agents.entries.dev.sandbox.mode='off'"
  note "and gate exec behind approvals instead"
fi

# ── Gateway ─────────────────────────────────────────────────────────────────
sect "Gateway"

GW_STATUS="$(openclaw gateway status 2>&1 || true)"
if grep -qiE 'listen|running|healthy|ok' <<<"$GW_STATUS"; then
  ok "gateway responding"
  if grep -qE '127\.0\.0\.1|loopback|localhost' <<<"$GW_STATUS"; then
    ok "bound to loopback"
  else
    warn "could not confirm loopback binding — check gateway.bind"
    note "exposing the gateway beyond loopback requires auth and a tunnel"
  fi
else
  bad "gateway not responding"
  note "try: openclaw gateway restart"
fi

BIND="$(openclaw config get gateway.bind 2>/dev/null | tr -d '"'"'"' ' || true)"
[[ -n "$BIND" ]] && note "gateway.bind = ${BIND}"

AUTH_MODE="$(openclaw config get gateway.auth.mode 2>/dev/null | tr -d '"'"'"' ' || true)"
case "$AUTH_MODE" in
  token|password) ok "gateway auth mode: ${AUTH_MODE}" ;;
  ""|null|none)   bad "gateway auth not set — set gateway.auth.mode to 'token'" ;;
  *)              warn "gateway auth mode: ${AUTH_MODE}" ;;
esac

# ── Config ──────────────────────────────────────────────────────────────────
sect "Config"

if [[ -f "$CONFIG_PATH" ]]; then
  ok "config present at ${CONFIG_PATH}"

  if [[ -L "$CONFIG_PATH" ]]; then
    bad "config is a symlink — OpenClaw requires a regular file (atomic rename)"
  fi

  PERMS="$(stat -f '%Lp' "$CONFIG_PATH" 2>/dev/null || stat -c '%a' "$CONFIG_PATH" 2>/dev/null)"
  if [[ "$PERMS" == "600" ]]; then
    ok "config permissions 600"
  else
    bad "config permissions ${PERMS} — should be 600"
    note "fix: openclaw security audit --fix"
  fi

  # Unreplaced template placeholders
  if grep -qE 'YOUR_[A-Z_]+' "$CONFIG_PATH" 2>/dev/null; then
    bad "config still contains ALL_CAPS placeholders:"
    grep -oE 'YOUR_[A-Z_]+' "$CONFIG_PATH" | sort -u | sed 's/^/        /'
  else
    ok "no unreplaced placeholders"
  fi
else
  bad "no config at ${CONFIG_PATH}"
fi

DIR_PERMS="$(stat -f '%Lp' "$OPENCLAW_HOME" 2>/dev/null || stat -c '%a' "$OPENCLAW_HOME" 2>/dev/null)"
if [[ "$DIR_PERMS" == "700" ]]; then
  ok "state dir permissions 700"
else
  warn "state dir permissions ${DIR_PERMS} — should be 700"
fi

# ── Secrets (presence only, never values) ───────────────────────────────────
sect "Secrets"

for key in OPENROUTER_API_KEY DISCORD_BOT_TOKEN GITHUB_TOKEN; do
  VAL="$(openclaw config get "env.${key}" 2>/dev/null || true)"
  if [[ -n "$VAL" && "$VAL" != "null" && "$VAL" != '""' ]]; then
    ok "${key} is set"
  elif [[ -n "${!key:-}" ]]; then
    warn "${key} set in this shell but not in config"
    note "the daemon has its own environment; set it via:"
    note "  openclaw config set env.${key} \"...\""
  else
    case "$key" in
      GITHUB_TOKEN) warn "${key} not set (only needed for the dev agent)" ;;
      *)            bad "${key} not set" ;;
    esac
  fi
done

# ── Agents ──────────────────────────────────────────────────────────────────
sect "Agents"

for agent in main triage dev; do
  if openclaw config get "agents.entries.${agent}" >/dev/null 2>&1; then
    ok "agent '${agent}' configured"
  else
    warn "agent '${agent}' not found — three-agent trust split not in place"
  fi
done

# The invariant: untrusted-content readers must not be able to execute.
for agent in main triage; do
  EXEC="$(openclaw config get "agents.entries.${agent}.tools.exec.security" 2>/dev/null | tr -d '"'"'"' ' || true)"
  if [[ "$EXEC" == "deny" ]]; then
    ok "'${agent}' exec denied"
  else
    bad "'${agent}' exec is '${EXEC:-unset}' — must be 'deny'"
    note "this breaks the core invariant: content from outside must never"
    note "reach an agent that can execute"
  fi
done

SANDBOX="$(openclaw config get agents.entries.dev.sandbox.mode 2>/dev/null | tr -d '"'"'"' ' || true)"
case "$SANDBOX" in
  all|non-main) ok "'dev' sandboxed (mode: ${SANDBOX})" ;;
  *)            warn "'dev' sandbox mode is '${SANDBOX:-unset}'"
                note "unsandboxed exec needs an approvals flow — see docs/security.md" ;;
esac

# ── Workspace identity ──────────────────────────────────────────────────────
sect "Workspace"

for f in SOUL.md IDENTITY.md USER.md AGENTS.md; do
  if [[ -f "${WORKSPACE}/${f}" ]]; then
    ok "${f}"
  else
    warn "${f} missing from ${WORKSPACE}"
  fi
done

if [[ -f "${WORKSPACE}/USER.md" ]]; then
  # Template ships with empty "- **Field:**" lines and italic guidance.
  if grep -qE '^\*\*This is a template' "${WORKSPACE}/USER.md"; then
    bad "USER.md is still the unedited template"
    note "this is the highest-leverage hour in the whole setup — fill it in"
  else
    FILLED="$(grep -cE '^\s*-\s+\S' "${WORKSPACE}/USER.md" 2>/dev/null || echo 0)"
    if (( FILLED < 8 )); then
      warn "USER.md looks sparse (${FILLED} filled lines)"
      note "escalation rules and people-who-matter are what make it feel like yours"
    else
      ok "USER.md filled in (${FILLED} entries)"
    fi
  fi
fi

[[ -f "${WORKSPACE}/MEMORY.md" ]] && ok "MEMORY.md present (long-term memory active)" \
  || note "MEMORY.md not yet created — it appears once memory accumulates"

# ── Channels ────────────────────────────────────────────────────────────────
sect "Channels"

DM_POLICY="$(openclaw config get channels.discord.dmPolicy 2>/dev/null | tr -d '"'"'"' ' || true)"
case "$DM_POLICY" in
  pairing|allowlist) ok "discord dmPolicy: ${DM_POLICY}" ;;
  open)              bad "discord dmPolicy is 'open' — anyone can DM your assistant" ;;
  "")                warn "discord not configured" ;;
  *)                 warn "discord dmPolicy: ${DM_POLICY}" ;;
esac

PREVIEWS="$(openclaw config get channels.discord.linkPreviews 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$PREVIEWS" == "false" ]]; then
  ok "discord link previews disabled"
else
  warn "discord link previews not disabled"
  note "link unfurling is an indirect prompt-injection vector"
fi

# ── Automation ──────────────────────────────────────────────────────────────
sect "Automation"

CRON_OUT="$(openclaw cron list 2>/dev/null || true)"
if [[ -n "$CRON_OUT" ]]; then
  JOB_COUNT="$(grep -cE '^\s*[0-9a-zA-Z_-]{6,}' <<<"$CRON_OUT" || echo 0)"
  ok "cron jobs installed (~${JOB_COUNT})"
else
  warn "no cron jobs found — run: bash openclaw/cron-jobs.sh"
fi

TRIGGERS="$(openclaw config get cron.triggers.enabled 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$TRIGGERS" == "true" ]]; then
  bad "cron.triggers.enabled is ON"
  note "this permits headless script execution with the owning agent's full"
  note "tool policy, including exec. Turn it off unless you deliberately need it."
else
  ok "cron.triggers.enabled off"
fi

HB_TARGET="$(openclaw config get heartbeat.target 2>/dev/null | tr -d '"'"'"' ' || true)"
[[ -n "$HB_TARGET" && "$HB_TARGET" != "null" ]] \
  && ok "heartbeat target set" \
  || warn "heartbeat target not set"

HB_HOURS="$(openclaw config get heartbeat.activeHours 2>/dev/null || true)"
[[ -n "$HB_HOURS" && "$HB_HOURS" != "null" ]] \
  && ok "heartbeat activeHours set (no 3am pings)" \
  || warn "heartbeat activeHours not set — it may ping you overnight"

# ── MCP ─────────────────────────────────────────────────────────────────────
sect "MCP servers"

MCP_OUT="$(openclaw mcp status 2>/dev/null || true)"
if [[ -n "$MCP_OUT" ]]; then
  ok "mcp servers registered"
  note "deep check: openclaw mcp doctor --probe"
else
  warn "no MCP servers registered — see docs/integrations.md"
fi

# ── macOS sleep posture ─────────────────────────────────────────────────────
if [[ "$(uname -s)" == "Darwin" ]]; then
  sect "macOS sleep posture"
  note "cron runs inside the gateway process; a sleeping Mac misses jobs"
  if pmset -g sched 2>/dev/null | grep -qi 'wake'; then
    ok "a scheduled wake is configured"
  else
    warn "no scheduled wake"
    note "sudo pmset repeat wakeorpoweron MTWRFSU 06:45:00"
  fi
  if pmset -g custom 2>/dev/null | grep -A20 'AC Power' | grep -qE '\bsleep\s+0'; then
    ok "no automatic sleep on AC power"
  else
    warn "Mac sleeps on AC power"
    note "System Settings > Battery > Options > Prevent automatic sleeping"
  fi
fi

# ── Summary ─────────────────────────────────────────────────────────────────
sect "Summary"
printf '  %d passed, %d warnings, %d failures\n\n' "$PASS" "$WARN" "$FAIL"

if (( FAIL > 0 )); then
  echo "  Fix the ✗ items before connecting real accounts."
  echo "  Then run: openclaw security audit"
  exit 1
elif (( WARN > 0 )); then
  echo "  Working, with gaps. Review the ! items."
  exit 2
else
  echo "  Setup looks complete."
  exit 0
fi
