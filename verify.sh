#!/usr/bin/env bash
# Verify an OpenClaw setup end to end.
#
#   bash ./verify.sh
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
  echo; echo "Nothing else to check. Run: bash ./bootstrap.sh"; exit 1
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

  # MCP isolation is enforced by tools.allow globs. There is no
  # agents.entries.<id>.mcp.servers key — if the config uses one, every agent
  # silently sees every registered server.
  if openclaw config get "agents.entries.${agent}.mcp.servers" >/dev/null 2>&1; then
    bad "'${agent}' uses agents.entries.${agent}.mcp.servers — not a real key"
    note "MCP isolation must use tools.allow globs, e.g. [\"gmail__*\"]"
    note "as written, this agent can reach EVERY registered MCP server"
  fi

  ALLOW="$(openclaw config get "agents.entries.${agent}.tools.allow" 2>/dev/null || true)"
  if [[ -n "$ALLOW" && "$ALLOW" != "null" ]]; then
    ok "'${agent}' has a tools.allow list"
  else
    warn "'${agent}' has no tools.allow — it can reach every MCP server"
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

SCOPE="$(openclaw config get agents.entries.dev.sandbox.scope 2>/dev/null | tr -d '"'"'"' ' || true)"
case "$SCOPE" in
  session) ok "'dev' sandbox scope: session" ;;
  shared)  bad "'dev' sandbox scope is 'shared' — sessions share one container" ;;
  *)       warn "'dev' sandbox scope is '${SCOPE:-unset}' (defaults to 'agent')"
           note "'agent' means every dev session shares one container; prefer 'session'" ;;
esac

ELEVATED="$(openclaw config get tools.elevated.enabled 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$ELEVATED" == "true" ]]; then
  bad "tools.elevated.enabled is ON — elevated exec runs outside the sandbox"
else
  ok "elevated exec disabled"
fi

FSONLY="$(openclaw config get tools.fs.workspaceOnly 2>/dev/null | tr -d '"'"'"' ' || true)"
[[ "$FSONLY" == "true" ]] && ok "filesystem access confined to workspace" \
  || warn "tools.fs.workspaceOnly not set"

ORIGINS="$(openclaw config get gateway.controlUi.allowedOrigins 2>/dev/null || true)"
if [[ -n "$ORIGINS" && "$ORIGINS" != "null" ]]; then
  ok "control UI allowedOrigins set"
else
  bad "gateway.controlUi.allowedOrigins not set (critical audit finding)"
  note "any page on another localhost port presents a loopback origin and passes"
fi

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

# There is no `linkPreviews` key. Per-channel: Discord suppressEmbeds (defaults
# true), Slack unfurlLinks/unfurlMedia (default false), Telegram linkPreview
# (defaults ON — the one that actually needs setting).
if openclaw config get channels.discord.linkPreviews >/dev/null 2>&1; then
  bad "channels.discord.linkPreviews is not a real key — silently ignored"
  note "use suppressEmbeds for Discord (already defaults to true)"
fi

EMBEDS="$(openclaw config get channels.discord.suppressEmbeds 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$EMBEDS" == "false" ]]; then
  bad "discord suppressEmbeds is off — link unfurling is an injection vector"
else
  ok "discord link unfurling suppressed"
fi

TG_PREVIEW="$(openclaw config get channels.telegram.linkPreview 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ -n "$TG_PREVIEW" && "$TG_PREVIEW" != "false" ]]; then
  bad "telegram linkPreview is on (it defaults ON) — set it false"
fi

# channels.<x>.agent does nothing; binding is the top-level bindings[] array.
for ch in discord slack telegram; do
  if openclaw config get "channels.${ch}.agent" >/dev/null 2>&1; then
    bad "channels.${ch}.agent is not a real key — silently ignored"
    note "bind agents via the top-level bindings[] array"
  fi
done

if openclaw config get bindings >/dev/null 2>&1; then
  ok "bindings[] present (channel to agent routing)"
else
  warn "no bindings[] — channels fall through to the default agent"
fi

# ── Automation ──────────────────────────────────────────────────────────────
sect "Automation"

CRON_OUT="$(openclaw cron list 2>/dev/null || true)"
if [[ -n "$CRON_OUT" ]]; then
  JOB_COUNT="$(grep -cE '^\s*[0-9a-zA-Z_-]{6,}' <<<"$CRON_OUT" || echo 0)"
  ok "cron jobs installed (~${JOB_COUNT})"
else
  warn "no cron jobs found — run: bash ./cron-jobs.sh"
fi

TRIGGERS="$(openclaw config get cron.triggers.enabled 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$TRIGGERS" == "true" ]]; then
  bad "cron.triggers.enabled is ON"
  note "this permits headless script execution with the owning agent's full"
  note "tool policy, including exec. Turn it off unless you deliberately need it."
else
  ok "cron.triggers.enabled off"
fi

# Heartbeat lives at agents.defaults.heartbeat — a top-level block is not read.
if openclaw config get heartbeat >/dev/null 2>&1; then
  bad "top-level 'heartbeat' block found — OpenClaw does not read it"
  note "move it under agents.defaults.heartbeat"
fi

HB_TARGET="$(openclaw config get agents.defaults.heartbeat.target 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ -n "$HB_TARGET" && "$HB_TARGET" != "null" && "$HB_TARGET" != "none" ]]; then
  ok "heartbeat target set (${HB_TARGET})"
else
  warn "heartbeat target unset or 'none' — it runs but delivers nothing"
fi

HB_HOURS="$(openclaw config get agents.defaults.heartbeat.activeHours 2>/dev/null || true)"
if [[ -n "$HB_HOURS" && "$HB_HOURS" != "null" ]]; then
  if grep -q '"tz"' <<<"$HB_HOURS"; then
    bad "activeHours uses 'tz' — the field is 'timezone'"
  else
    ok "heartbeat activeHours set (no 3am pings)"
  fi
else
  warn "heartbeat activeHours not set — it may ping you overnight"
fi

# If any agent defines its own heartbeat block, ONLY those agents heartbeat.
for agent in main triage dev; do
  if openclaw config get "agents.entries.${agent}.heartbeat" >/dev/null 2>&1; then
    warn "agent '${agent}' defines its own heartbeat block"
    note "when any agent does this, ONLY those agents run heartbeats"
  fi
done

# ── Coding harnesses (ACP) ──────────────────────────────────────────────────
sect "Coding harnesses (ACP)"

ACP_ENABLED="$(openclaw config get acp.enabled 2>/dev/null | tr -d '"'"'"' ' || true)"
if [[ "$ACP_ENABLED" == "true" ]]; then
  ok "ACP enabled"

  if openclaw acp doctor >/dev/null 2>&1; then
    ok "ACP backend healthy"
  else
    warn "ACP backend not healthy — run: openclaw acp doctor"
  fi

  ALLOWED="$(openclaw config get acp.allowedAgents 2>/dev/null || true)"
  if [[ -n "$ALLOWED" && "$ALLOWED" != "null" ]]; then
    ok "acp.allowedAgents set (${ALLOWED//[$'\n' ]/})"
  else
    bad "acp.allowedAgents not set — any supported harness can be spawned"
  fi

  PERM="$(openclaw config get plugins.entries.acpx.config.permissionMode 2>/dev/null | tr -d '"'"'"' ' || true)"
  case "$PERM" in
    approve-all)
      warn "permissionMode: approve-all"
      note "only safe if every session runs in a throwaway worktree"
      note "spawn via worktree.sh, never against a live working copy" ;;
    strict)
      ok "permissionMode: strict"
      note "note: non-interactive sessions can't answer prompts, so cron-"
      note "and phone-driven runs will stall" ;;
    deny) ok "permissionMode: deny" ;;
    *)    warn "permissionMode unset (${PERM:-none})" ;;
  esac

  if command -v opencode >/dev/null 2>&1; then
    ok "opencode CLI on PATH"
  else
    bad "opencode not installed — the harness runs as its own CLI"
    note "brew install opencode   # then: opencode -> /connect"
  fi

  # Worktree isolation is the actual containment boundary for ACP work.
  WT_ROOT="${WORKTREE_ROOT:-$HOME/code/worktrees}"
  if [[ -d "$WT_ROOT" ]]; then
    WT_COUNT="$(find "$WT_ROOT" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l | tr -d ' ')"
    ok "worktree root exists (${WT_COUNT} active)"
  else
    note "no worktree root yet at ${WT_ROOT} — created on first `make code`"
  fi
else
  note "ACP not enabled — external coding harnesses unavailable"
  note "enable with: make acp"
fi

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
