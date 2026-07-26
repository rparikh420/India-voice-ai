# OpenClaw E2E Setup Plan — Curated Personal AI Assistant

A complete, opinionated blueprint for standing up **OpenClaw v2026.7.x** as an always-available
personal assistant that handles inbox + calendar, developer workflow, daily life ops, and
knowledge capture.

**Target profile (decided up front):**

| Decision | Choice |
|---|---|
| Gateway host | macOS laptop/desktop (native Hub app + launchd) |
| Primary channel | Discord (personal), Slack added later for work routing |
| Model provider | OpenRouter — Kimi K2.6 primary, cheap open models for routine work |
| Automation scope | Inbox + calendar, dev workflow, daily life ops, knowledge + notes |

---

## Read this first: two constraints that shape everything

### 1. A laptop is not an always-on host

OpenClaw's cron scheduler **runs inside the Gateway process**. When your Mac sleeps, the Gateway
sleeps: scheduled jobs do not fire, and inbound Discord messages queue at the platform rather
than being answered. This is the single biggest gap between "assistant on a laptop" and
"assistant that runs your life."

Mitigations, in order of preference:

- **Keep it awake on AC power.** System Settings → Battery → Options → *Prevent automatic sleeping
  on power adapter*. Combined with `Wake for network access`, this covers a desk-bound Mac well.
- **Schedule wake windows** so morning jobs fire even from sleep:
  ```bash
  sudo pmset repeat wakeorpoweron MTWRFSU 06:45:00
  ```
- **Wrap the Gateway in `caffeinate`** if you want it pinned awake while running (see
  `bootstrap.sh`).
- **Accept catch-up semantics.** Design cron jobs to be idempotent and to summarise "what I missed"
  rather than assuming they fired on the dot. The Phase 6 jobs below are written this way.

If, after a few weeks, the missed-job friction annoys you, Phase 9 has a migration path to a
€5/mo VPS that preserves all state. Don't start there — start local, learn the system, migrate
once you know what you actually use.

### 2. Cheap open models degrade agentic reliability, so route by task

OpenRouter + Kimi is a great cost story, but multi-step tool use is exactly where smaller models
fall down. The fix is **tiered routing**, not one model everywhere: a capable model for the main
agentic loop, a cheap fast model for the high-frequency low-stakes turns (heartbeats, triage
classification), and explicit escalation for anything that writes to the outside world. Phase 2
implements this.

---

## Architecture: three agents, not one

The most important design decision in this whole plan. A single all-powerful agent that reads your
email *and* has shell access is a prompt-injection accident waiting to happen — a malicious email
becomes instructions your agent executes with your credentials.

Split by trust level instead:

```
┌───────────────────────────────────────────────────────────────────┐
│ main — "the assistant"                                            │
│   Talks to you on Discord. Memory on. MCP: Calendar, Notion.      │
│   exec: DENIED. Can delegate to the others.                       │
├───────────────────────────────────────────────────────────────────┤
│ triage — "reads untrusted things"                                 │
│   Gmail + web fetch. Low privilege. exec: DENIED.                 │
│   CANNOT message externally. Output is data, not instructions.    │
├───────────────────────────────────────────────────────────────────┤
│ dev — "does the work"                                             │
│   Repo workspace. exec ALLOWED but sandboxed. GitHub MCP.         │
│   No email, no calendar, no personal memory.                      │
└───────────────────────────────────────────────────────────────────┘
```

Rule of thumb: **content that arrives from outside never reaches an agent that can execute.**
`triage` reads your inbox and returns structured summaries; `main` acts on those summaries; `dev`
touches code in a sandbox. This is the pattern the OpenClaw security docs describe as running
untrusted content in a low-privilege agent, and it costs you nothing but config.

The split works because summarization is a **serialization boundary** — an attacker who controls
an email body does not control the summary `triage` emits, so they lose control of exact tokens
before anything reaches an agent with tools. That blunts injection; it does not eliminate it.

Three limits to hold in mind from the start:

- **Isolation is enforced by `tools.allow` globs, not by which servers you registered.** There is
  no `agents.entries.<id>.mcp.servers` key. Omit a glob and that agent inherits *every* registered
  MCP server. This control fails **open**.
- **MCP servers are not sandboxed.** Containment covers `exec`, `read`, `write`, `edit`,
  `apply_patch`, and `process` — not MCP. Servers and plugins run in-process with Gateway
  credentials, so a compromised MCP server is outside the blast-radius model entirely. This is the
  largest un-mitigated gap in the architecture.
- **`triage` runs the cheapest model on the most hostile input.** A deliberate trade, documented
  in the config and in `docs/security.md` §7, with a condition attached: widen `triage`'s tools and
  you upgrade its model in the same commit.

---

## Phase 1 — Install and harden the Gateway

**Goal:** a running, locked-down Gateway before it ever touches your data.

### 1.1 Prerequisites

```bash
node --version    # need 24.15+ (recommended), or 22.22.3+ / 25.9+
brew install node@24
```

### 1.2 Install

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
# or: npm install -g openclaw@latest
```

> Piping a remote script to bash is the vendor's documented path. If you'd rather inspect it first:
> `curl -fsSL https://openclaw.ai/install.sh -o install.sh && less install.sh && bash install.sh`

### 1.3 Onboard with the daemon

```bash
openclaw onboard --install-daemon
```

This walks you through provider selection and installs the launchd unit so the Gateway survives
reboots. Take the defaults for now — Phase 2 replaces the model config properly.

### 1.4 Harden **before** connecting anything

This is the step people skip and regret. Apply the baseline from `openclaw.config.json5`:

```json5
{
  gateway: {
    bind: "loopback",              // 127.0.0.1 only — not reachable from your LAN
    auth: { mode: "token" },       // defense in depth if bind ever changes
    controlUi: { allowedOrigins: ["http://127.0.0.1:18789"] },
  },
  tools: {
    profile: "messaging",          // minimal tool surface by default
    exec: { security: "deny", ask: "always" },
    elevated: { enabled: false },  // elevated exec bypasses the sandbox entirely
    fs: { workspaceOnly: true },
    deny: ["group:automation", "group:runtime", "group:fs",
           "sessions_spawn", "sessions_send"],
  },
  session: { dmScope: "per-channel-peer" },
}
```

Then verify:

```bash
openclaw security audit          # review findings
openclaw security audit --fix    # tightens file perms to 600/700
openclaw gateway status          # expect: listening on 127.0.0.1:18789
```

**Known risk worth understanding.** Missing WebSocket origin validation was
[CVE-2026-25253](https://nvd.nist.gov/vuln/detail/CVE-2026-25253) (CVSS 8.8): a malicious web page
could reach `localhost:18789` from inside your own browser and exfiltrate the gateway token, giving
RCE. **Fixed in v2026.1.29**, so any current v2026.7.x install is patched.

The residual still matters. Current validation accepts a request when *both* origin and request are
loopback — so any page served from another localhost port (a dev server, a stray
`python -m http.server` in your downloads folder) presents a loopback origin and passes. Set
`gateway.controlUi.allowedOrigins` explicitly; its absence is itself a critical audit finding.
`docs/security.md` T12 has a curl one-liner that tests both cases.

Loopback binding is not an access control. Keep `auth.mode: "token"` on and keep OpenClaw updated.

### 1.5 macOS Hub app

Install the signed macOS app for the menu-bar UI, Quick Chat (⌥-Space), Canvas, and voice input.
In local Gateway mode the app owns the launchd Gateway and keeps versions aligned across updates.

You'll be prompted for Screen Recording, Microphone, Speech Recognition, Automation, and
Accessibility permissions. **Grant only what you'll use** — decline Screen Recording and
Accessibility unless you specifically want screen-aware or UI-driving features. You can always
add them later.

**Exit criteria:** `openclaw gateway status` is green, `openclaw security audit` is clean, dashboard
loads at `http://127.0.0.1:18789`, and you've had one successful chat.

---

## Phase 2 — Model routing on OpenRouter

**Goal:** good agentic reliability at open-model prices.

### 2.1 Authenticate

```bash
openclaw onboard --auth-choice openrouter-api-key
# or OAuth: openclaw onboard --auth-choice openrouter-oauth
```

### 2.2 Tiered routing

Model refs follow `openrouter/<provider>/<model>`.

| Tier | Model | Used for |
|---|---|---|
| Primary | `openrouter/moonshotai/kimi-k2.6` | Main agentic loop, tool use, dev work |
| Fallback | `openrouter/moonshotai/kimi-k2.5` | Primary unavailable |
| Cheap | `openrouter/google/gemini-3.5-flash-lite` | Heartbeats, triage classification, summarisation |
| Escape hatch | `openrouter/auto` | Let OpenRouter route when you don't care |

`openrouter/auto`, `kimi-k2.6`, and `kimi-k2.5` are OpenClaw's bundled fallbacks, so they resolve
even when live catalog discovery is down. Everything else resolves dynamically against
OpenRouter's catalog.

Config lives in `openclaw.config.json5` under `agents.defaults.model` with per-agent overrides —
`triage` and heartbeat runs get the cheap tier, `main` and `dev` get Kimi.

### 2.3 Calibrate honestly

Spend a week noticing where it fails. Open models tend to break on **long multi-tool chains** and
**strict output formats**. When you find a task that fails repeatedly, don't fight the prompt —
pin that one task to a stronger model. Cost control is about routing, not about refusing to ever
spend.

**Exit criteria:** a 3-step tool-use task (search → fetch → summarise into Notion) completes
end-to-end without hand-holding.

---

## Phase 3 — Identity: make it actually *yours*

**Goal:** the difference between "a chatbot I own" and "my assistant."

OpenClaw injects these workspace bootstrap files into the system prompt's Project Context on each
new session's first turn. They live at the workspace root (default `~/.openclaw/workspace`) and
starter versions are in `workspace/` here.

| File | Purpose |
|---|---|
| `SOUL.md` | Persona, boundaries, tone — how it behaves |
| `IDENTITY.md` | Name, vibe, emoji — who it is |
| `USER.md` | Your profile, how you want to be addressed, context about your life |
| `AGENTS.md` | Operating instructions + working memory — the rules of engagement |
| `MEMORY.md` | Root long-term memory (only injected when it exists) |

Copy them in:

```bash
cp openclaw/workspace/*.md ~/.openclaw/workspace/
```

**The highest-leverage thing you will do in this entire plan** is spend an hour writing a real
`USER.md`. Your timezone, your working hours, who matters to you, what you're building, what you
want escalated at 2am vs. what waits until morning, how blunt you want it to be. The onboarding
wizard will also interview you to build these definitions — let it, then edit by hand.

### 3.1 Enable cross-conversation memory

```json5
memory: {
  search: { rememberAcrossConversations: true }
}
```

On by default for personal installs, restricted to private conversations (group/channel sessions
can't read cross-conversation transcripts). **Active Memory** is a blocking memory sub-agent that
injects relevant memories before the model responds, so recall happens without you asking.

Tuning that matters: `queryMode: "recent"` for conversational balance, `maxSummaryChars` (default
220) down if recall gets noisy, `promptStyle` chosen from `balanced` / `strict` / `contextual` /
`recall-heavy` / `precision-heavy` / `preference-only`.

Memory works well for stable preferences, recurring habits, and long-term context. It works badly
for automation and one-shot tasks — which is why the Phase 6 cron jobs run in isolated sessions.

**Exit criteria:** start a fresh session, ask "what do you know about me?", and get something that
sounds like it's been paying attention.

---

## Phase 4 — Discord as the primary channel

**Goal:** talk to your assistant from your phone, with the door locked.

Discord over Slack for the personal tier: no workspace admin needed, bot tokens are trivial, DMs
work cleanly, and it's free. Slack joins in Phase 7 for work/dev routing.

### 4.1 Create the bot

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** → set username → **Reset Token** → copy it
3. Enable **Privileged Gateway Intents**: Message Content (required), Server Members (recommended)
4. **OAuth2 URL Generator** → scopes `bot` + `applications.commands`
5. Permissions: View Channels, Send Messages, Read Message History, Embed Links, Attach Files,
   Send Messages in Threads
6. Invite it to a **private server that only you are in**

```bash
export DISCORD_BOT_TOKEN="..."   # store in your shell profile or a secrets manager
```

### 4.2 Lock it to you

```json5
channels: {
  discord: {
    enabled: true,
    token: { source: "env", provider: "default", id: "DISCORD_BOT_TOKEN" },
    dmPolicy: "pairing",        // DM access requires an approval code
    groupPolicy: "allowlist",   // only servers you list
    guilds: {
      "YOUR_SERVER_ID": { requireMention: false, users: ["YOUR_USER_ID"] },
    },
  },
}
```

Get IDs via User Settings → Advanced → Developer Mode, then right-click server/avatar → Copy ID.

**Also: turn off link previews.** Both in Discord's settings and in the OpenClaw channel config.
Link unfurling is a documented indirect prompt-injection vector — a link in a message gets fetched
and its content lands in your agent's context without you ever clicking anything.

### 4.3 Channel topology

Create one private server with purpose-scoped channels — this gives you free context separation:

- `#assistant` — general chat, the default session
- `#brief` — morning brief and scheduled digests (write-mostly)
- `#inbox` — email triage output
- `#dev` — CI, PRs, build failures
- `#capture` — quick notes that flow into Notion/Obsidian

**Exit criteria:** you DM the bot from your phone and get a useful answer.

---

## Phase 5 — Integrations via MCP

**Goal:** give it hands. Gmail, Calendar, Notion, GitHub.

OpenClaw is an MCP client, so anything with an MCP server plugs in.

```bash
# Filesystem, scoped narrowly and read-mostly.
# Note: read_file was split into read_text_file / read_media_file.
openclaw mcp add files --command npx --arg -y \
  --arg @modelcontextprotocol/server-filesystem --arg "$HOME/Documents" \
  --include 'read_text_file,read_media_file,list_directory'

# GitHub (dev agent). @modelcontextprotocol/server-github is DEPRECATED and
# archived — use GitHub's own remote server.
openclaw mcp add github --url https://api.githubcopilot.com/mcp/ \
  --transport streamable-http --auth oauth
openclaw mcp login github

# Remote HTTP servers with OAuth
openclaw mcp add notion --url https://mcp.notion.com/mcp --transport streamable-http --auth oauth
openclaw mcp login notion
```

**Registering a server does not scope it to an agent.** There is no
`agents.entries.<id>.mcp.servers` key — isolation is enforced by `tools.allow`
globs on each agent (`["gmail__*"]`, `["github__*"]`, …), and sandboxed agents
need the tools re-admitted inside the sandbox via
`tools.sandbox.tools.alsoAllow`. Skip this and every agent reaches every server,
which defeats the entire three-agent split. See `docs/integrations.md` for the
full three-gate model.

Verify everything:

```bash
openclaw mcp doctor --probe
openclaw mcp probe notion --json
```

### 5.1 Two non-negotiable rules

**Allowlist tools, always.** Every MCP server ships more tools than you need, and each one is
attack surface. Filter before agents ever see them:

```bash
openclaw mcp configure gmail --include 'search_*,get_message,create_draft' --exclude 'send_*'
```

Note `--exclude 'send_*'` — let it *draft* email, not *send* it. Sending is a one-way door. Promote
to autonomous sending only after weeks of watching its drafts, and even then keep it scoped to
specific recipients.

**Scope servers to the agent that needs them.** Gmail belongs to `triage`. GitHub belongs to `dev`.
Calendar and Notion belong to `main`. Nothing gets the union.

### 5.2 Credentials

`~/.openclaw/` holds tokens, credentials, and private transcripts. Turn on **FileVault**,
keep permissions at 600/700 (`openclaw security audit --fix`), and back it up encrypted (Phase 9).
OpenClaw blocks interpreter-startup env hooks (`NODE_OPTIONS`, `PYTHONSTARTUP`, `LD_*`, `DYLD_*`)
but permits credential vars like `GITHUB_TOKEN` — so treat that directory as a secret store.

**Exit criteria:** "What's on my calendar tomorrow, and is anything conflicting?" returns a real
answer.

---

## Phase 6 — Automations that earn their keep

**Goal:** it does things without being asked.

Two mechanisms:

- **Cron** — persisted in SQLite, survives restarts. Types: `at`, `every`, `cron`, `on-exit`,
  `stream`. Delivery: `announce` (to a channel), `webhook`, or `none`.
- **Heartbeat** — periodic agent turns in the main session (default every 30m) that surface things
  needing attention. Replies `HEARTBEAT_OK` and stays silent when nothing's up.

Run `cron-jobs.sh` to install the starter set. Highlights:

| Job | Schedule | What it does |
|---|---|---|
| Morning brief | 07:00 weekdays | Calendar + inbox + weather + top 3 priorities → `#brief` |
| Inbox triage | Every 2h, 08:00–20:00 | `triage` classifies new mail, drafts replies, flags urgent |
| Evening review | 21:00 daily | What happened, what slipped, tomorrow's setup → `#brief` |
| Weekly review | Sun 17:00 | Week in review, memory curation prompt, next week's plan |
| CI watch | `on-exit` / webhook | Build failures → `#dev` with a diagnosis |
| Capture sweep | 22:00 daily | Loose notes from `#capture` → filed into Notion |

Key flags and settings:

```bash
openclaw cron create "0 7 * * 1-5" --name "Morning brief" \
  --session isolated --tz America/New_York \
  --message "..." --deliver announce --target discord:CHANNEL_ID
```

- `--session isolated` gives a fresh session per run. **Use it for almost every job** — it drops
  token cost from ~100K to ~2–5K by skipping the accumulated conversation.
- `--tz` matters: timestamps without a timezone default to UTC, and cron expressions without one
  use the host timezone.
- Recurring top-of-hour jobs auto-stagger by 5 minutes to spread load.

### 6.1 Heartbeat config

```json5
heartbeat: {
  every: "30m",
  target: "discord:YOUR_CHANNEL_ID",
  isolatedSession: true,
  lightContext: true,
  activeHours: { start: "08:00", end: "22:00", tz: "America/New_York" },
}
```

`activeHours` is what stops it from pinging you at 3am.

### 6.2 A deliberate omission: `cron.triggers.enabled`

Leave it **off** (the default). Enabling it permits headless script execution with the owning
agent's full tool policy — including `exec`. That's unattended automation running shell commands
with your permissions, triggered by conditions the model evaluates. Turn it on only for a specific
job you've thought hard about, never as a blanket setting.

**Exit criteria:** you wake up to a morning brief you'd actually read, three days running.

---

## Phase 7 — Developer workflow

**Goal:** the `dev` agent does real work in a blast radius you control.

### 7.1 Sandbox it

```json5
agents: {
  entries: {
    dev: {
      workspace: "~/code",
      sandbox: { mode: "all", workspaceAccess: "rw" },
      tools: { exec: { security: "allow" } },
    },
  },
}
```

`sandbox.mode: "all"` runs every session contained; `workspaceAccess: "rw"` mounts the workspace at
`/workspace`. Requires Docker running on the Mac. Tool policy applies *before* sandbox rules — a
globally denied tool stays denied, so the layers compose rather than override.

If you don't want Docker, set `sandbox.mode: "off"` and instead keep `exec` on an approvals flow
(`openclaw approvals`) so risky commands need a tap from you. Slower, but honest — running
unsandboxed `exec` with no approvals on a machine that has your SSH keys is not a defensible setup.

### 7.2 Add Slack for work

Same shape as Discord, `allowlist` DM policy, scoped to your work workspace. Route `#dev` traffic
there if that's where your team lives. Keep personal (Discord) and work (Slack) on separate agents
so work context never leaks into personal memory.

### 7.3 Useful patterns

- **PR triage:** cron every 30m → `dev` lists open PRs needing your review → digest to `#dev`
- **CI autofix:** GitHub webhook → hook endpoint → `dev` diagnoses the failure, pushes a fix branch
- **Repo watch:** on push to main, summarise what changed for the `#dev` channel
- **Drive coding agents from chat:** v2026.7.1 strengthened Codex and connected coding-agent
  workflows — kick off a task from your phone, review the diff when you're back at the desk

Webhooks come in via the `hooks` config block, which supports a shared token, path scoping, session
key allowlists, and per-mapping routing to specific agents.

**Exit criteria:** a CI failure produces a useful diagnosis in `#dev` before you've noticed it.

---

## Phase 8 — Skills: teach it your specifics

A skill is a directory with a `SKILL.md` (YAML frontmatter + markdown body) that teaches the agent
how and when to use tools. Discovery goes up to 6 levels deep, and precedence runs:

1. Workspace skills (`<workspace>/skills`)
2. Project agent skills (`<workspace>/.agents/skills`)
3. Personal agent skills (`~/.agents/skills`)
4. Managed/local (`~/.openclaw/skills`)
5. Bundled
6. Extra dirs and plugin skills

Highest source wins.

```bash
openclaw skills install @owner/slug            # from ClawHub
openclaw skills install @owner/slug --global
openclaw skills verify @owner/slug             # checks the trust envelope
openclaw skills check                          # missing bins/env/config — run after copying skills in
openclaw skills update --all
```

Three format details that bite:

- **`description` is load-bearing.** Only the skill *list* — name, description, location — enters
  the system prompt; the body loads on demand. If the description doesn't describe when to reach
  for the skill, it never triggers. Keep it to one line under 160 characters.
- **`metadata` is not plain YAML.** It's parsed as YAML, then flattened to a string and re-parsed
  as JSON5. Write it in the JSON5 shape the docs use. A hand-written YAML mapping under `metadata:`
  is the standard way to get silent gating failures.
- **Skills cannot restrict their own tools.** There's no `allowed-tools` field — that's a Claude
  Code thing, not an OpenClaw one. A skill that shouldn't touch the shell can't enforce that
  itself; agent tool policy and the sandbox have to.

Also worth knowing: per-agent `skills` allowlists **replace** the defaults rather than merging with
them, and `skills.entries.<name>.env` / `apiKey` inject into the host process only — a sandboxed
`dev` agent won't see them, so pass secrets through container setup instead.

### 8.1 Treat community skills as untrusted code

Because they are. **Read the source before enabling.** Refuse anything that wants broad filesystem
access or fetches and executes remote code at runtime. `openclaw skills verify` checks ClawHub's
trust envelope and exits non-zero on failure — wire it into your update routine. Consider
`security.installPolicy` to run a trusted policy command before installs proceed.

**Know where the trust check stops.** Risky community releases require an explicit
`--acknowledge-clawhub-risk` flag, which is good. But official publishers and bundled sources
bypass verification entirely — "official" is a publisher claim, not an audit. Read those too.

Skills snapshot at session start, so changes land in the *next* session. There is a mid-session
refresh when `SKILL.md` files change or a new node connects, but don't rely on it while iterating —
restart the session to test.

### 8.2 Write your own

The highest-value skills are the ones nobody else can write: how *you* want your email triaged,
your project's deploy runbook, your writing voice. Six are included in `skills/` — copy them in
with `make skills`, then edit. They are starting points, not finished products; the rules in them
are guesses about your preferences until you correct them.

| Skill | Agent | What it encodes |
|---|---|---|
| `email-triage` | `triage` | Urgent/Reply/FYI/Noise rules, draft-never-send, injection reporting |
| `morning-brief` | `main` | Brief assembly, ordered by decision-need, with asleep-laptop catch-up |
| `meeting-notes` | `main` | Note structure, action items with owners and dates, Notion filing |
| `weekly-review` | `main` | Weekly retro plus memory curation against `MEMORY.md` |
| `deploy-runbook` | `dev` | This repo's deploy path, as a worked example to adapt |
| `escalation-policy` | all | One interrupt/queue/silent rule shared by every proactive path |

`escalation-policy` is the one to read first. Every proactive path — the 30-minute heartbeat, six
cron jobs, triage output, CI webhooks — independently decides whether to speak, and the failure
mode is silent: nobody reports a slightly noisy assistant, they just stop reading it, and then
none of the rest of this matters.

See `skills/README.md` for the full format reference.

---

## Phase 9 — Operations

### Updating

```bash
openclaw --version
openclaw update                   # NOT `npm i -g` — see below
openclaw security audit           # re-audit after every update
```

Use `openclaw update` rather than reinstalling by hand. It detects the install
type, runs `openclaw doctor`, and coordinates the package swap with the running
Gateway service; a manual `npm install -g` against a supervised install can load
core files mid-swap.

Releases move fast (v2026.7.1 landed 3,063 contributions from 532 contributors). Check the
changelog before updating — this is a project where minor versions change behaviour.

### Backup

`~/.openclaw/` is the whole system: config, credentials, SQLite session stores, workspace. Back it
up **encrypted** — it contains your private transcripts.

```bash
tar czf - ~/.openclaw | age -r <your-key> > openclaw-$(date +%F).tar.gz.age
```

Sessions live at `~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`.

### Audit cadence

- **Weekly:** `openclaw security audit`, skim cron job list, review what memory absorbed
- **Monthly:** re-check MCP tool allowlists, prune skills you don't use, rotate the gateway token
- **After every update:** re-audit; new features sometimes ship new surface

### If you migrate to a VPS later

Install, then restore `~/.openclaw/` and re-pair channels. Keep `bind: "loopback"` and reach it
over **Tailscale Serve** or an SSH tunnel rather than exposing a port. Auth becomes mandatory once
you bind to `lan` or `tailnet`. Hetzner, Oracle Always-Free, and Fly.io are all documented as
working; enable `NODE_COMPILE_CACHE` on small or ARM hosts to cut cold-start pain.

---

## Suggested sequencing

| Week | Focus | Outcome |
|---|---|---|
| 1 | Phases 1–3 | Hardened gateway, models routed, it knows who you are |
| 2 | Phases 4–5 | Discord + Gmail/Calendar/Notion/GitHub wired |
| 3 | Phase 6 | Morning brief, inbox triage, evening review running |
| 4 | Phases 7–8 | Dev agent sandboxed, first custom skills |
| Ongoing | Phase 9 | Weekly audit, monthly prune, memory curation |

Resist doing it all in one weekend. Each phase changes how you work, and you need a few days of
living with it to know whether the automation is helping or just generating noise you'll learn to
ignore.

---

## Files in this directory

| File | What it is |
|---|---|
| `README.md` | This plan |
| `Makefile` | Task runner — `make help` for the setup order |
| `bootstrap.sh` | Guided install + hardening for macOS |
| `verify.sh` | Read-only end-to-end setup check — `make verify` |
| `openclaw.config.json5` | Full starter config: 3 agents, tiered models, locked-down channels |
| `cron-jobs.sh` | Installs the starter automation set |
| `workspace/SOUL.md` | Persona and boundaries |
| `workspace/IDENTITY.md` | Name and vibe |
| `workspace/USER.md` | Your profile — **fill this in properly** |
| `workspace/AGENTS.md` | Operating instructions |
| `skills/` | Six custom skills + format reference |
| `docs/channels.md` | Discord depth, full Slack walkthrough, voice and mobile |
| `docs/integrations.md` | MCP wiring for Gmail, Calendar, Notion, GitHub |
| `docs/security.md` | Threat model, injection test suite, incident response |
| `docs/operations.md` | Cadence, cost model, troubleshooting, VPS migration |

---

## Sources

- [OpenClaw on GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw docs](https://docs.openclaw.ai/)
- [Getting started](https://docs.openclaw.ai/start/getting-started)
- [Security](https://docs.openclaw.ai/gateway/security)
- [Configuration](https://docs.openclaw.ai/gateway/configuration)
- [Cron jobs](https://docs.openclaw.ai/automation/cron-jobs)
- [Skills](https://docs.openclaw.ai/tools/skills)
- [MCP client registry](https://docs.openclaw.ai/cli/mcp)
- [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)
- [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat)
- [Active memory](https://docs.openclaw.ai/concepts/active-memory)
- [OpenRouter provider](https://docs.openclaw.ai/providers/openrouter)
- [Discord channel](https://docs.openclaw.ai/channels/discord)
- [Running on a VPS](https://docs.openclaw.ai/vps)
- [v2026.7.1 release notes](https://github.com/openclaw/openclaw/releases/tag/v2026.7.1)
- [Repello AI — secure deployment checklist](https://repello.ai/blog/technical-best-practices-to-securely-deploy-openclaw)
- [Nebius — OpenClaw security architecture and hardening](https://nebius.com/blog/posts/openclaw-security)
