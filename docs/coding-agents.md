# Coding agents: running Claude Code and OpenCode through OpenClaw

Your primary use case is building software. That makes this document the centre of the setup, not
an appendix to it — and it changes one of the plan's core assumptions.

OpenClaw can do coding work two fundamentally different ways:

- **Its own embedded runtime** does the work itself — the `dev` agent from
  [Phase 7](../README.md#phase-7--developer-workflow), running Kimi K2.6 through OpenRouter, inside
  a Docker sandbox, with OpenClaw's tool policy and approvals in front of every shell command.
- **ACP (Agent Client Protocol)** makes OpenClaw a *control plane* that launches a real external
  coding harness — Claude Code, OpenCode, Codex, Gemini CLI — as a host process, and routes your
  Discord messages into it.

The second one is what you actually want for real coding work. It is also the one that runs
**outside every containment layer this project has built**. Section 6 is the most important part of
this document; if you read one section, read that one.

`openclaw.config.json5` is already wired for this: an `acp` block with `allowedAgents: ["opencode"]`,
an `opencode` agent entry, a `#code` Discord binding, and `permissionMode: "approve-all"` held in
check by [`worktree.sh`](../worktree.sh). This document explains why each of those is set the way it
is, and what it would take to add Claude Code beside it.

---

## Corrections found while researching this

The ACP feature list this document was drafted from is mostly accurate. Six things are wrong or
imprecise enough to break a config.

| Claim | Actually |
|---|---|
| `permissionMode` is `approve-all` \| `deny` \| `strict` | The values are **`approve-all`**, **`approve-reads`** (the default), and **`deny-all`**. `deny` is a value of a *different* key, `nonInteractivePermissions` (`fail` \| `deny`, default `fail`). `strict` is not a `permissionMode` value at all — it is an example argument to `/acp permissions <profile>`, which forwards a profile name to whatever vocabulary the harness itself advertises. |
| `mode` is `"persistent"` \| `"oneshot"` | Two vocabularies, and mixing them fails. `/acp spawn --mode` and `bindings[].acp.mode` take **`persistent`\|`oneshot`**. The `sessions_spawn` tool takes **`mode: "run"\|"session"`** — `run` is one-shot, `session` is persistent. `mode: "session"` additionally **requires `thread: true`**. |
| Harnesses include both "Droid" and "Factory" | One harness, id **`droid`** (Factory Droid CLI). `factory-droid` and `factorydroid` resolve to the same adapter. |
| That harness list is complete | It omits **`fast-agent`** (fetched via `uvx`) and **`pi`** (Pi Coding Agent — registered, but the docs say it "is not a coding harness in the same sense as the others"). |
| cwd precedence ends at "global ACP defaults → backend defaults" | There is an extra step. For a cross-agent spawn with no explicit `cwd`, OpenClaw inherits the **target agent's `workspace`** — a plain `agents.entries.<id>.workspace`, not a `runtime.acp.cwd`. A missing inherited path (`ENOENT`/`ENOTDIR`) silently falls back to the backend default; other access errors (`EACCES`) fail the spawn loudly. |
| Agent entries use `agents.entries[].runtime` | Both shapes appear in upstream docs — the prose says `agents.entries.*.runtime`, the worked example uses an `agents.list[]` array with `id` fields. This project uses the keyed `agents.entries.<id>` form everywhere else, so that is what the examples below use. If a config edit is silently ignored, this is the first thing to check. |

Two things the brief did not mention that materially change the security picture, both covered in
[§6](#6-the-security-section):

- `/acp` runtime controls (`spawn`, `close`, `model`, `permissions`, `cwd`, …) require **owner
  identity** from external channels like Discord, and `operator.admin` from internal Gateway
  clients. Non-owners get `sessions`, `doctor`, `install`, and `help` only.
- OpenClaw **hides ACP from the agent entirely** unless it is genuinely usable — enabled, dispatch
  on, requester not sandboxed, backend healthy. The model is never told about a backend it cannot
  reach, so it cannot suggest one.

---

## 1. What ACP is, and when to use it

There are four ways to get a coding task done in this system, and they are easy to confuse because
three of them involve the word "Claude" or "Codex".

| Route | Who owns the model loop | Sandboxable | Use when |
|---|---|---|---|
| **Embedded runtime** (`dev` agent) | OpenClaw | **Yes** — Docker, `sandbox.mode: "all"` | Untrusted repos, CI triage, anything reading content you didn't write |
| **ACP harness** (`/acp spawn claude`) | Claude Code / OpenCode / Codex | **No** — host process | Real feature work in repos you trust, driven from your phone |
| **CLI backend** (`agentRuntime.id: "claude-cli"`) | OpenClaw, executing through a local CLI | Inherits agent policy | You want a local CLI as a text-only fallback model, with no harness tooling |
| **`openclaw acp` bridge** | OpenClaw | Inherits agent policy | An IDE should drive OpenClaw — the reverse direction, [§7](#7-the-reverse-direction-ide--openclaw) |

The distinction that matters: **ACP is not a model choice, it is a runtime choice.** When you run
Claude Code through ACP, OpenClaw is not calling a model. It is launching the `claude` binary on
your Mac, handing it a working directory, and piping your Discord messages to it. Claude Code does
its own planning, its own file edits, its own shell commands, with its own model and its own
Anthropic credentials. OpenClaw owns routing, bindings, background-task state, delivery, and
policy gates. It owns nothing inside the harness.

That division is the whole story, and every other section follows from it.

### When the embedded `dev` agent is right

- The input is untrusted. A GitHub issue body, a CI log, a dependency's README — anything from
  [§1.2 or T7 of `security.md`](./security.md#12-hostile-web-content-reaching-an-agent).
- You want `exec` behind approvals, or contained in a container with no network.
- The task is small and mechanical: summarise a diff, list failing tests, draft a commit message.
- You want the work billed to OpenRouter alongside everything else.

### When ACP is right

- You are writing real code and want a harness that is *good at it* — Claude Code's or OpenCode's
  agentic loop, not Kimi K2.6 driving OpenClaw's generic tool loop.
- The repo is yours and you trust its contents.
- You want to kick a task off from your phone and review the diff at your desk. This is the
  killer use case and it is genuinely excellent.
- You want session resume: start at the laptop, continue from Discord, pick up where you left off.

### The rule

> Untrusted input goes to `dev`. Trusted repos go to ACP. Nothing that read an email or a web page
> ever decides to spawn an ACP session.

That last clause is not a style preference. See [§6.5](#65-the-injection-path-that-matters).

### A note on Codex specifically

If you ever enable the bundled `codex` plugin, **do not drive Codex through ACP.** Codex has a
native app-server path with its own command surface — `/codex bind`, `/codex threads`,
`/codex model`, `/codex steer`, `/codex stop` — which supports OpenClaw hook relay for
`before_tool_call` / `after_tool_call`, routes Codex `PermissionRequest` events through OpenClaw
approvals, and keeps a transcript mirror. ACP Codex has none of that. Upstream is explicit: *"Prefer
the native route unless you explicitly need ACP/acpx behavior."*

Claude Code and OpenCode have no native path. ACP is the only route for them, and that is the
setup this document is written for.

---

## 2. Harness support

All ids below are valid `/acp spawn <id>` and `sessions_spawn({ agentId })` targets under the
`acpx` backend.

| Id | Harness | Prerequisite (per upstream) | `session/load` resume | Verdict for this setup |
|---|---|---|---|---|
| `claude` | Claude Code | Claude Code auth on the host | **Confirmed** | **Primary.** Best agentic coding loop, confirmed resume, and the one to bind to `#dev`. |
| `opencode` | OpenCode | OpenCode CLI / provider auth | Not documented | **Secondary.** Open-source, bring-your-own-provider — the only harness that can share your OpenRouter key. |
| `codex` | Codex CLI | Codex/ChatGPT auth on the host | **Confirmed** | Use the native `/codex` path instead. ACP Codex is an explicit fallback only. |
| `gemini` | Gemini CLI | Gemini CLI auth or API key | Not documented | Viable third. Slow to start — see the 120s startup timeout in [§3.4](#34-timeouts). |
| `copilot` | GitHub Copilot CLI | Copilot CLI / runtime auth | Not documented | Only if you already pay for Copilot. |
| `cursor` | Cursor CLI (`cursor-agent acp`) | Cursor CLI install + auth | Not documented | Override the acpx command if your install exposes `agent acp` instead. |
| `droid` | Factory Droid | Factory/Droid auth or `FACTORY_API_KEY` | Not documented | Aliases: `factory-droid`, `factorydroid`. |
| `kimi` | Kimi CLI (Moonshot) | Kimi/Moonshot auth on the host | Not documented | Note: this is the *CLI harness*, unrelated to your OpenRouter Kimi K2.6 model. See [§8](#8-model-routing-and-cost). |
| `qwen` | Qwen Code | Qwen-compatible auth | Not documented | — |
| `fast-agent` | fast-agent | Fetched on demand via `uvx` (needs Python tooling) | Not documented | Omitted from the brief; needs `uv` on the host. |
| `mux` | Mux | Fetched on demand via `npx` | Not documented | — |
| `iflow` | iFlow CLI | Depends on installed CLI | Not documented | Upstream says model control "depends on the installed CLI". |
| `kilocode` | Kilocode | Depends on installed CLI | Not documented | Same caveat. |
| `kiro` | Kiro CLI | Depends on installed CLI | Not documented | Same caveat. |
| `qoder` | Qoder CLI | Depends on installed CLI | Not documented | Same caveat. |
| `trae` | Trae CLI | Depends on installed CLI | Not documented | Same caveat. |
| `openclaw` | OpenClaw ACP bridge | A reachable Gateway | n/a | Not a coding harness — this is the reverse direction, [§7](#7-the-reverse-direction-ide--openclaw). |
| `pi` | Pi Coding Agent | — | Not documented | Registered in acpx, but upstream states it "is not a coding harness in the same sense as the others". |

**Read the resume column carefully.** Upstream commits to exactly one sentence: *"The target agent
must support `session/load` (Codex and Claude Code do)."* Every "not documented" above means
precisely that — not "no", but *unverified*. If `resumeSessionId` matters to your workflow, the only
two harnesses you can count on today are `claude` and `codex`. Test before you build a habit on it;
a failed resume errors out cleanly rather than silently starting fresh, so the test is cheap.

**Prerequisites are thinner than they look.** Upstream documents *that* each harness needs its own
auth on the Gateway host, not *how* to get it. There is no per-harness install matrix in the docs,
and this document does not invent one. The reliable procedure is the same for all of them:

```bash
# install the harness's own CLI, however that vendor ships it
# then authenticate it as yourself, interactively, at a real terminal
claude          # or: opencode, gemini, codex, cursor-agent, droid
# confirm it works standalone before involving OpenClaw at all
```

If the CLI works when you run it by hand in a repo, ACP will work. If it doesn't, `/acp doctor` will
report a vendor auth error and you will spend an hour blaming OpenClaw for it.

### Recommendation for a Claude-Code-and-OpenCode setup

`openclaw.config.json5` currently ships `allowedAgents: ["opencode"]` — one harness, deliberately.
That is a defensible starting point, and the reasoning in the config comment is right: a one-entry
allowlist means a model cannot decide some other harness would be convenient. Add `claude` when you
want the better agentic loop, and not before you have authenticated the Claude Code CLI.

| | Choice | Shipped config |
|---|---|---|
| Primary harness | `claude` — best coding loop, confirmed `session/load` resume | not yet added |
| Second harness | `opencode` — the only harness that can reuse your OpenRouter key ([§8](#8-model-routing-and-cost)) | **active** |
| `acp.defaultAgent` | `claude` once added; otherwise `opencode` | unset |
| `acp.allowedAgents` | `["claude", "opencode"]` and nothing else | `["opencode"]` |
| `probeAgent` | Match the primary; otherwise `/acp doctor` lies to you | unset — see below |

That last row is a real trap. The `acpx` startup probe defaults to the first entry in
`acp.allowedAgents`, or to `codex` when that list is unset. The shipped config sets
`allowedAgents: ["opencode"]` and no `probeAgent`, so the probe currently resolves to `opencode` —
correct today, and silently wrong the moment you prepend `claude` to that list without installing
the Claude Code CLI. Set `probeAgent` explicitly rather than relying on list order.

---

## 3. Setup

### 3.1 Install and enable the backend

```bash
openclaw plugins install @openclaw/acpx
openclaw config set plugins.entries.acpx.enabled true
openclaw gateway restart
```

Then, from chat or the TUI:

```text
/acp doctor
```

`/acp doctor` is the single check that matters. It probes the embedded runtime, reports capabilities,
and gives actionable fixes. Run it after every change in this section.

The `acpx` plugin **embeds the ACP runtime directly** — there is no separate `acpx` binary to install
or version to pin. It registers the backend during Gateway startup and blocks the `ready` signal on a
startup probe. (`OPENCLAW_ACPX_RUNTIME_STARTUP_PROBE=0` disables that wait; only do this for scripted
environments that intentionally skip it.)

> **If you ever set `plugins.allow`, it must include `acpx`.** `plugins.allow` is a restrictive
> inventory, not an additive one. Omit `acpx` and the installed, enabled backend is deliberately
> blocked — `/acp doctor` will name the missing allowlist entry, which is the only reason this is
> merely annoying rather than baffling.

### 3.2 Adapter fetching

acpx auto-downloads ACP adapters via `npx` on first use, so you do not install adapter packages by
hand. Two consequences:

- **First spawn of a harness is slow** and needs network. Pre-warm it once at your desk rather than
  discovering it from your phone on a train.
- **A host with no npm or no network cannot fetch adapters at all.** Relevant if you follow the
  README's Phase 9 VPS migration path.

The Codex adapter ships with the plugin and launches locally. It also runs with an isolated
`CODEX_HOME`: OpenClaw copies trusted project entries and safe model/provider routing config from
your host Codex config, but **auth, notifications, and hooks stay on the host config only**.

### 3.3 Per-harness prerequisites

Each harness needs its own CLI installed and authenticated **on the Gateway host** — the Mac running
OpenClaw. This is worth stating precisely because it surprises people: the auth is *not* OpenClaw's.
Your OpenRouter key is irrelevant to Claude Code. Claude Code uses your Anthropic subscription or
API key, stored wherever the Claude Code CLI keeps it, under your macOS user account.

Checklist per harness, in order:

1. The CLI binary exists and is on the Gateway process's `PATH`.
2. It is authenticated for your user (`claude`, `opencode auth`, `gemini`, etc. — vendor-specific).
3. A plain interactive run in a real repo succeeds.
4. `/acp doctor` is green.
5. The id is present in `acp.allowedAgents`.

Step 1 is the one that bites on macOS. A launchd-managed Gateway does **not** inherit your shell
profile, so a harness installed via a version manager (`nvm`, `mise`, `asdf`) may be invisible to it
even though it works fine in Terminal. If `/acp doctor` reports "harness command not found" while
the command clearly exists, that is what happened. Either install the CLI somewhere on the system
`PATH`, or pin the command explicitly:

```json5
{
  plugins: {
    entries: {
      acpx: {
        enabled: true,
        config: {
          agents: {
            claude: { command: "/opt/homebrew/bin/claude" },
            opencode: { command: "/opt/homebrew/bin/opencode" },
          },
        },
      },
    },
  },
}
```

`agents.<id>.command` is the executable or command string; the optional `agents.<id>.args` array is
shell-quoted item by item, which is how you keep a path containing spaces as one argv token.

### 3.4 Timeouts

Two different timeouts, frequently confused:

| Setting | Covers | Default |
|---|---|---|
| `plugins.entries.acpx.config.timeoutSeconds` | Runtime **startup** and control operations | 120s |
| `agents.defaults.subagents.runTimeoutSeconds` | Child **turn** limit for `sessions_spawn` ACP runs | — |
| `/acp timeout <seconds>` | Runtime turn timeout for one live session | — |

The 120-second default exists because Gemini CLI is slow to complete ACP startup. Raise it if your
host is slower; restart the Gateway after changing it. `sessions_spawn` deliberately **rejects**
per-call timeout overrides (`runTimeoutSeconds` / `timeoutSeconds` return a config-the-default
error), so the operator path is the agent default.

---

## 4. Config reference

### 4.1 The baseline

`openclaw.config.json5` already carries a working ACP block. This is that block with the optional
keys it omits filled in and annotated:

```json5
{
  acp: {
    enabled: true,
    dispatch: { enabled: true },   // false pauses automatic thread dispatch,
                                   // but explicit sessions_spawn still works
    backend: "acpx",
    defaultAgent: "opencode",      // not set in the shipped config
    // The allowlist is the cheapest real control you have. Two entries, not
    // seventeen. Every id you add is a CLI whose own permission model you are
    // now trusting on this host. Shipped config has ["opencode"] only.
    allowedAgents: ["claude", "opencode"],
    stream: { deliveryMode: "live" },
  },

  plugins: {
    entries: {
      acpx: {
        enabled: true,
        config: {
          // The shipped config sets approve-all. That is the right call HERE
          // and only here — it is defensible because worktree.sh confines
          // every session to a throwaway checkout. Read §6.3 and §6.6 before
          // you keep it, and especially before you point a session at a live
          // working copy.
          permissionMode: "approve-all",
          // Not set in the shipped config, so it defaults to `fail`. Harmless
          // alongside approve-all, since nothing prompts. Matters the moment
          // you tighten permissionMode.
          nonInteractivePermissions: "fail",
          // Otherwise defaults to the first allowedAgents entry, or `codex`
          // when allowedAgents is unset — a false failure if Codex isn't
          // installed. Set it to whichever harness you actually installed.
          probeAgent: "opencode",
          timeoutSeconds: 120,
          // Both MCP bridges are default-off. Leave them off. See §6.4.
          pluginToolsMcpBridge: false,
          openClawToolsMcpBridge: false,
        },
      },
    },
  },

  // Required for --thread auto|here on Discord and Telegram.
  session: {
    threadBindings: {
      enabled: true,
      idleHours: 24,
      maxAgeHours: 0,
      spawnSessions: true,
    },
  },
}
```

### 4.2 Agent entries

The original three agents were `main`, `triage`, and `dev`. **None of them can run ACP**, and `dev`
least of all — it is sandboxed, and sandboxed sessions are hard-blocked from spawning ACP sessions.
So the config now carries a fourth entry, `opencode`, whose defining property is that it is honest
about being uncontained:

```json5
{
  agents: {
    entries: {
      // ── opencode: external coding harness via ACP ────────────────────────
      // NOT an OpenClaw agent in the usual sense. OpenClaw is only the control
      // plane; OpenCode runs as its own CLI on the host with its own model
      // config, its own auth, and its own permission model.
      //
      // OpenClaw's sandbox does NOT wrap ACP execution. The `sandbox` block
      // that protects `dev` does nothing here. Containment is `cwd` — which is
      // why every session is pointed at a throwaway worktree. See §6.
      opencode: {
        runtime: {
          type: "acp",
          acp: {
            agent: "opencode",        // harness id from §2
            backend: "acpx",
            mode: "persistent",       // persistent | oneshot (NOT run | session)
            cwd: "~/code/worktrees",  // parent dir; worktree.sh overrides
                                      // per-spawn with an isolated --cwd
          },
        },
      },
    },
  },
}
```

To add Claude Code alongside it, add `"claude"` to `acp.allowedAgents` and a second entry of the
same shape:

```json5
{
  agents: {
    entries: {
      claudecode: {
        runtime: {
          type: "acp",
          acp: { agent: "claude", backend: "acpx", mode: "persistent",
                 cwd: "~/code/worktrees" },
        },
        // This agent's own OpenClaw model barely matters — the harness runs
        // its own loop with its own model. This only covers turns where
        // OpenClaw itself answers. And deliberately no `subagents.model`: see §8.
        model: { primary: "openrouter/moonshotai/kimi-k2.6" },
        tools: {
          profile: "messaging",
          exec: { security: "deny" },   // OpenClaw-side exec. Does NOT constrain
                                        // the harness. See §6.2.
        },
        memory: { search: { rememberAcrossConversations: false } },
      },
    },
  },
}
```

Note what these entries deliberately lack: no email, no calendar, no cross-conversation memory, and
no `sandbox` block — because a `sandbox` block here would be decorative, and a decorative security
control is worse than none. Keep `dev` exactly as it is. It remains the right tool for untrusted
input, and it is the only coding path in this setup that is actually contained.

### 4.3 Bindings

Persistent bindings live in the **top-level `bindings[]` array** — the same array that already routes
`discord → main`. Order matters: matching runs most-specific-first, so ACP bindings for individual
channels must appear **before** the broad channel route.

This is the shipped `#code` binding, plus an optional second one for Claude Code:

```json5
{
  bindings: [
    // ── ACP bindings first: narrow before broad ──────────────────────────
    // #code is the coding surface: messages there drive an OpenCode session
    // instead of talking to the assistant.
    {
      type: "acp",
      agentId: "opencode",
      match: {
        channel: "discord",
        peer: { kind: "channel", id: "YOUR_CODE_CHANNEL_ID" },
      },
      acp: { label: "opencode-main" },
    },
    // Optional second surface once `claude` is in acp.allowedAgents.
    {
      type: "acp",
      agentId: "claudecode",
      match: {
        channel: "discord",
        accountId: "default",
        peer: { kind: "channel", id: "YOUR_CLAUDE_CHANNEL_ID" },
      },
      acp: { label: "claude-main" },
    },

    // ── then the existing broad route ────────────────────────────────────
    { channel: "discord", agent: "main" },
  ],
}
```

Neither binding sets `acp.cwd`, which is deliberate: `worktree.sh` supplies an isolated `--cwd` per
spawn, and a hardcoded binding `cwd` would quietly override it for messages that arrive outside that
flow. If you ever bind a channel directly to a fixed directory, make sure it is a worktree and not a
live checkout — see [§6.6](#66-containment-options-ranked).

`bindings[].acp` accepts `label`, `cwd`, `mode`, and `backend`. Peer id shapes are per-channel:

| Channel | `match.peer.id` |
|---|---|
| Discord | `<channelOrThreadId>` |
| Slack | `<channelId>`, `channel:<id>`, `#<id>`, `<userId>`, `user:<id>`, `<@userId>` — channel bindings also match replies in that channel's threads |
| Telegram | `<chatId>:topic:<topicId>` |
| WhatsApp | E.164 (`+15555550123`) or group JID (`…@g.us`) |
| iMessage | `<handle>`, `chat_id:*`, `chat_guid:*`, `chat_identifier:*` — prefer `chat_id:*` for groups |

Once bound: every message in that channel goes to the ACP session. `/new` and `/reset` reset the
same ACP session key in place rather than making a new one. Channel broadcast fan-out does not
override a matched ACP binding. Bindings persist across Gateway restarts.

### 4.4 The cwd precedence chain

Five steps, highest wins:

1. **`/acp spawn --cwd <path>`** or `sessions_spawn({ cwd })` — explicit, per-spawn.
2. **`bindings[].acp.cwd`** — per-conversation.
3. **`agents.entries.<id>.runtime.acp.cwd`** — per-agent ACP default.
4. **`agents.entries.<id>.workspace`** — the target agent's plain workspace, inherited on
   cross-agent spawns with no explicit `cwd`. *(This is the step the brief omitted.)*
5. **Backend default cwd.**

The failure semantics of step 4 are worth knowing because they are asymmetric: a **missing** path
(`ENOENT` / `ENOTDIR`) silently degrades to the backend default, while a **permission** error
(`EACCES`) surfaces as a spawn error. So a typo in `workspace` does not fail loudly — it quietly
runs your harness somewhere else. Set `cwd` explicitly on every binding and never rely on inheritance.

### 4.5 Where the project's existing config gets in the way

Your current `openclaw.config.json5` has a global `tools.deny` that includes `sessions_spawn` and
`sessions_send`. Consequences:

- **`/acp spawn` still works.** It is a Gateway slash command gated on owner identity, not a tool.
- **`sessions_spawn({ runtime: "acp" })` does not.** The agent cannot start ACP sessions on its own
  initiative — it can only work in sessions *you* spawned.

That is a good default and you should keep it. It means every ACP session in this setup begins with
a deliberate human action, which is precisely the property [§6.5](#65-the-injection-path-that-matters)
depends on. Only lift it for the `code` agent, and only when you specifically want agent-initiated
background runs.

---

## 5. Session lifecycle

### 5.1 The loop

```text
/acp spawn claude --bind here --cwd /Users/you/code/api
… work in the channel …
/acp status
/acp steer also update the OpenAPI spec
/acp model anthropic/claude-opus-4-6
/acp cancel                       # abort this turn, keep the session
/acp close                        # end session, remove binding
```

`spawn` creates or resumes a runtime session, records ACP metadata in the OpenClaw session store,
and may create a background task when the run is parent-owned. `cancel` aborts the in-flight turn
where the backend supports cancellation, leaving binding and metadata intact. `close` ends the
session from OpenClaw's side and removes the binding — though the harness may keep its own upstream
history if it supports resume.

**Gateway commands stay local.** `/acp …`, `/status`, and `/unfocus` are intercepted by OpenClaw and
never forwarded as prompt text to a bound harness. You cannot accidentally ask Claude Code what
`/acp status` means.

### 5.2 Bind vs thread

`--bind here` and `--thread …` are mutually exclusive.

| Flag | Behaviour |
|---|---|
| `--bind here` | Pin the current conversation. No child thread. Fails if no active bindable conversation. |
| `--bind off` | No current-conversation binding. |
| `--thread auto` | In a thread: bind it. Outside one: create and bind a child thread where supported. |
| `--thread here` | Require an active thread; fail otherwise. |
| `--thread off` | Unbound session. |

Thread binding is adapter-specific — currently Discord threads/channels and Telegram topics — and
requires `session.threadBindings.spawnSessions=true`. On surfaces without thread support, the
default is effectively `off`, and OpenClaw returns a clear unsupported message rather than failing
mysteriously. `--bind here` is the simpler path and the one to reach for first.

### 5.3 Driving a coding task from your phone

This is the payoff, and it is worth walking through concretely. The examples below spawn `claude`
because that is the harness worth building the habit on; swap in `opencode` to run them against the
shipped `allowedAgents: ["opencode"]` as-is. A spawn naming a harness that is not in the allowlist
fails with `ACP agent "<id>" is not allowed by policy`, which is the intended behaviour, not a bug.

**A: one repo, one channel, always on.** Configure the persistent binding from
[§4.3](#43-bindings) once. Then `#dev` in Discord *is* Claude Code in `~/code/primary-project`,
permanently. From your phone:

```text
you:    the /health endpoint returns 200 even when postgres is down. fix it and add a test.
claude: [reads src/routes/health.ts, edits it, runs the suite, reports back in #dev]
you:    /acp steer use the existing DbHealth helper instead of a raw query
claude: [adjusts, re-runs]
you:    /acp close
```

Later, at your desk, `git diff` and review. Nothing was committed unless you asked for it.

**B: ad-hoc, on a different repo.** No config change needed:

```text
/acp spawn claude --bind here --cwd /Users/you/code/other-project --label hotfix
```

**C: a background one-shot while you keep chatting.** Spawn into a child thread so the main channel
stays usable:

```text
/acp spawn opencode --thread auto --mode oneshot --cwd /Users/you/code/api
```

Completion reports back through the task-announce path rather than taking over the channel.

**D: hand off from laptop to phone.** The `resumeSessionId` flow — the reason `claude` and `codex`
are the two harnesses worth building habits around:

```json
{
  "task": "Continue where we left off - fix the remaining test failures",
  "runtime": "acp",
  "agentId": "claude",
  "resumeSessionId": "<previous-session-id>"
}
```

The harness replays its conversation history via `session/load`, so it resumes with full context.
Notes that save time:

- `resumeSessionId` is a **host-local harness resume id**, not an OpenClaw session key. Get it from
  `/acp sessions`.
- It only applies when `runtime: "acp"`; the native sub-agent runtime ignores it.
- `thread` and `mode` still apply normally to the *new* OpenClaw session — so `mode: "session"`
  still requires `thread: true`.
- A missing id **fails with a clear error**. There is no silent fallback to a fresh session, which
  is the right behaviour: you find out immediately rather than watching a confused agent redo work.

### 5.4 Session hygiene

- Parent-owned one-shot sessions are cleaned up by task maintenance when terminal or orphaned.
- Persistent sessions survive while an active binding remains; stale persistent sessions with no
  binding are closed so they cannot be silently resumed later.
- acpx reaps OpenClaw-owned wrapper and adapter process trees on `close` and at Gateway startup.
- Idle runtime workers are cleaned up after the built-in idle period, but stored metadata stays
  available to `/acp sessions`.

Add `/acp sessions` to the weekly checks in [`operations.md`](./operations.md#1-operating-cadence).
An ACP session you forgot about is a harness process with your credentials and a working directory.

---

## 6. THE SECURITY SECTION

Read this before you enable anything above.

### 6.1 ACP runs outside the sandbox

Upstream states it flatly:

> ACP sessions currently run on the host runtime, **not** inside the OpenClaw sandbox.
>
> - The external harness can read/write according to its own CLI permissions and the selected `cwd`.
> - **OpenClaw's sandbox policy does not wrap ACP harness execution.**
> - OpenClaw still enforces ACP feature gates, allowed agents, session ownership, channel bindings,
>   and Gateway delivery policy.
> - Use `runtime: "subagent"` for sandbox-enforced OpenClaw-native work.

This project's architecture — [three agents split by trust level](../README.md#architecture-three-agents-not-one),
a Docker-sandboxed `dev`, `tools.elevated.enabled: false`, `fs.workspaceOnly`, a global exec deny —
was built on the assumption that the coding agent is contained. **For ACP, that assumption is
false.** Not weakened, not partially true. The containment layer is simply not in the path.

Two hard consequences, both enforced by OpenClaw rather than left to discipline:

- **Sandboxed sessions cannot spawn ACP sessions at all.** Both `sessions_spawn({ runtime: "acp" })`
  and `/acp spawn` are blocked from a sandboxed requester. Your `dev` agent has
  `sandbox.mode: "all"`, so `dev` can never use ACP. This is a feature: it prevents the mistake of
  believing a sandboxed agent's ACP child is also sandboxed.
- **`sessions_spawn` does not support `sandbox: "require"` for ACP.** There is no combination of
  flags that gets you a contained ACP session. Asking for one returns an error rather than
  pretending.

So ACP work needs its own uncontained agent — the `code` entry in [§4.2](#42-agent-entries) — and
that agent should be documented, in your own config, as uncontained.

### 6.2 What does and does not contain an ACP harness

The single most valuable table in this document.

| Control | Contains an ACP harness? | Why |
|---|---|---|
| `sandbox.mode: "all"` / Docker | **No** | Not in the execution path. Blocks the agent from *spawning*, contains nothing once spawned. |
| `tools.exec.security: "deny"` | **No** | Governs OpenClaw's `exec` tool. The harness runs its own shell, not OpenClaw's. |
| `tools.exec.mode` / `openclaw approvals` | **No** | Upstream: *"host exec approvals do not loosen ACPX harness prompts"* — and the converse. Independent layers. |
| `tools.fs.workspaceOnly` | **No** | Applies to OpenClaw fs tools. |
| `tools.deny` / `tools.allow` | **No** (for harness behaviour) | Controls which OpenClaw tools exist. Does gate `sessions_spawn`, so it controls *whether a spawn happens* — see [§4.5](#45-where-the-projects-existing-config-gets-in-the-way). |
| `tools.elevated.enabled: false` | **No** | Irrelevant; the harness is already on the host. |
| **The harness's own CLI permission model** | **Yes** | This is the real boundary. Claude Code's permission rules are what actually stand between the harness and your disk. |
| **`plugins.entries.acpx.config.permissionMode`** | **Partly** | Harness-level prompt policy for ACP sessions. Real, but coarse — see [§6.3](#63-permission-modes). |
| **The selected `cwd`** | **Partly** | Scopes the harness's default working set. Not a jail: a shell command can `cd` anywhere your user can. |
| **OS-level controls** (separate user, ACLs, FileVault) | **Yes** | Kernel-enforced. The only containment that holds against a harness running arbitrary shell. |
| `acp.allowedAgents` | **Yes**, for *which* harness | Limits which binaries can be launched. Says nothing about what they then do. |
| Owner-identity gate on `/acp` | **Yes**, for *who* | Runtime controls require owner identity externally, `operator.admin` internally. |

The pattern: everything in the left column that starts with `tools.` or `sandbox.` protects
*OpenClaw*. Nothing there protects your filesystem from Claude Code, because Claude Code is not
using OpenClaw's tools.

### 6.3 Permission modes

Two independent keys under `plugins.entries.acpx.config`:

| `permissionMode` | Behaviour |
|---|---|
| `approve-reads` | Auto-approve reads only; writes and exec require a prompt. **Default.** |
| `approve-all` | Auto-approve all file writes and shell commands. |
| `deny-all` | Deny all permission prompts. |

| `nonInteractivePermissions` | Behaviour when a prompt is required but no TTY exists |
|---|---|
| `fail` | Abort with `PermissionPromptUnavailableError`. **Default.** |
| `deny` | Silently deny and continue — graceful degradation. |

**ACP sessions are always non-interactive.** There is no TTY behind a Discord message. So these two
keys interact in a way that produces the single most common ACP complaint:

> With the defaults (`approve-reads` + `fail`), any write or shell command in an ACP session hits a
> permission prompt that cannot be shown, and the session **dies early with little output**.

There are exactly three honest resolutions, and you must pick one consciously:

1. **`approve-all`.** Write and exec work. This is the break-glass "yolo" switch: the harness now
   writes files and runs shell commands on your Mac with zero approval, driven by messages arriving
   over Discord. It is what most people end up running, and it is a real decision, not a formality.
2. **`approve-reads` + `nonInteractivePermissions: "deny"`.** The session degrades gracefully
   instead of crashing: reads work, writes and exec are refused, the harness carries on and tells
   you what it couldn't do. Excellent for a read-only "explain this codebase" agent. Useless for
   writing code.
3. **`deny-all`.** Effectively disables ACP for coding.

There is no fourth option where the harness writes code *and* you approve each command from your
phone. `/acp permissions <profile>` forwards a profile name to the harness's own vocabulary, and
what a given harness does with `strict` is up to that harness, not OpenClaw — do not plan around it.

**The recommendation for this setup:** `approve-all`, combined with the containment in
[§6.6](#66-containment-options-ranked) — because the containment has to come from the OS and the
`cwd`, not from a prompt nobody can answer.

### 6.4 Keep the MCP bridges off

By default, ACP harnesses see **none** of your OpenClaw tools. Two opt-in bridges change that:

| Setting | Exposes |
|---|---|
| `pluginToolsMcpBridge` | All plugin-registered tools from installed, enabled plugins |
| `openClawToolsMcpBridge` | Selected built-in tools — currently `cron` |

Leave both `false`. `pluginToolsMcpBridge` is all-or-nothing across your enabled plugins; upstream's
own guidance is to *"treat this as the same trust boundary as letting those plugins execute in
OpenClaw itself."* And `cron` is precisely how an injection achieves **persistence** — the exact
mechanism [`security.md` §2](./security.md#the-composition-rules-that-actually-matter) says to deny
even to `triage`. Handing `cron` to an external harness undoes that.

### 6.5 The injection path that matters

The three-agent split exists to guarantee one property: *content that arrives from outside never
reaches an agent that can execute*. ACP creates a new way to violate it, and the violation is
subtle because it happens one hop earlier than you'd look.

The dangerous sequence is not "harness gets injected." It is:

```
hostile content  →  an agent that can decide to spawn  →  /acp spawn with attacker-chosen cwd
```

If any agent that reads untrusted input can also initiate an ACP spawn, then a crafted email or
GitHub issue body can choose the **working directory** and the **task text** for an uncontained
host process. `cwd` is the whole blast radius. An attacker who picks it picks the target.

Three rules, in priority order:

1. **`triage` must never reach ACP.** It reads the most hostile input in the system and runs the
   cheapest model. Keep `sessions_spawn` denied globally, and never add `acp` capability to it.
2. **Never let ACP output flow back into a spawn decision.** A harness summarising a repo that
   contains hostile text is producing untrusted output. Treat it as data, exactly like `triage`'s
   summaries.
3. **Prefer operator-initiated spawns.** The default in [§4.5](#45-where-the-projects-existing-config-gets-in-the-way)
   — `sessions_spawn` globally denied, `/acp spawn` owner-gated — means every ACP session starts
   with a human typing a command. Keep it that way for as long as you can stand it.

OpenClaw helps here more than it has to: ACP guidance and the `runtime: "acp"` option are **hidden
from the model entirely** unless ACP is enabled, dispatch is on, the requester is unsandboxed, and
the backend is healthy. An agent that cannot use ACP is never told ACP exists, so it cannot be
talked into suggesting it. That is a genuine design win — but it gates on *capability*, not on
*trust*, so it does nothing once you have given an agent the capability.

### 6.6 Containment options, ranked

Ranked by how much they actually contain a harness that can run shell commands.

**1. A dedicated OS user.** The only kernel-enforced boundary available. Run the Gateway (or at
minimum the harness) as a user that cannot read `~/.ssh`, `~/.openclaw`, your browser profiles, or
your documents. Everything else on this list is defence in depth on top of this one; without it, a
harness running arbitrary shell has exactly your privileges. It is also the most work, which is why
it is rarely done — but it is the difference between "a bad command damaged a repo" and "a bad
command read my credential store."

**2. Per-project `cwd`, set explicitly, on every binding.** Never let `cwd` be inherited (see the
silent-fallback trap in [§4.4](#44-the-cwd-precedence-chain)). One repo per binding. This bounds the
harness's *default* working set and is what makes its file operations predictable. It does not stop
shell from leaving the directory.

**3. Git worktree isolation.** This is the boundary this project actually leans on, via
[`worktree.sh`](../worktree.sh):

```bash
bash openclaw/worktree.sh ~/code/India-voice-ai fix-tts-latency \
  "Diagnose the 400ms TTS gap in tts_coalesce.py and fix it. Run pytest."
```

Every session gets its own checkout on its own `agent/<slug>` branch under `~/code/worktrees`, and
the harness is pointed there with `--cwd`. Worst case is deleting a directory and a branch; your
working copy, staged changes, and `main` are untouched. **This is what makes
`permissionMode: "approve-all"` defensible rather than reckless** — and pointing a session at a live
working copy is exactly what would make it reckless again.

OpenClaw also has its own managed worktrees, which are a reasonable alternative:

```bash
openclaw worktrees create /Users/you/code/api --name acp-task --base-ref main
openclaw worktrees list
```

Those live under the OpenClaw state directory on branch `openclaw/<name>`, with snapshot-on-removal
restorable for 30 days. `worktree.sh` is the simpler, more explicit path and it carries `.env`
across (a fresh worktree otherwise fails tests in confusing ways); the managed ones give you
snapshots and a Control UI. Either is fine. Using neither is not.

Be precise about what this buys, because the config comment states it more absolutely than the
mechanism supports: a worktree bounds the **expected** blast radius — your working tree and branch
state — not your machine. A worktree shares the repository's git common directory, and a harness
with shell access can `cd` out of it and run any command your user can. It is an excellent default
that makes routine agent mistakes cheap and reviewable. It is not a kernel-enforced boundary, which
is why option 1 above still sits at the top of this list.

Two related traps: `.openclaw/worktree-setup.sh`, if present in a repo, **executes repository code**
on managed-worktree creation; and `worktree.sh` copying `.env` across means **your real secrets are
in the directory you just gave an agent write access to**. Both are consequences of the same rule —
trusted repos only.

**4. `permissionMode` and `nonInteractivePermissions`.** Real but coarse, per
[§6.3](#63-permission-modes). Use `approve-reads` + `deny` for any read-only ACP agent; accept
`approve-all` only for agents whose `cwd` and OS user you have already constrained.

**5. `acp.allowedAgents`.** Two entries. Each additional harness is another vendor's CLI, another
permission model, another auth store on your host. Cheap to set, meaningful in aggregate.

**6. Owner-only controls.** Already enforced: `/acp spawn|close|model|permissions|cwd|timeout` need
owner identity from Discord and `operator.admin` internally. Combined with Discord's
`dmPolicy: "pairing"` and the guild user allowlist from
[Phase 4](../README.md#42-lock-it-to-you), this is solid. Verify it holds after every update — the
[`security.md` T12](./security.md#t12--identity-gating-and-the-localhost-pivot) identity-gating test
covers the general case.

**7. Never letting untrusted content reach a spawn decision.** Architectural, free, and the one that
actually prevents the attack rather than limiting its damage. [§6.5](#65-the-injection-path-that-matters).

### 6.7 Update the threat model

Add to [`security.md` §8, "what this setup deliberately does not protect against"](./security.md#8-what-this-setup-deliberately-does-not-protect-against):

> **ACP harness execution.** Any ACP session runs on the host as the Gateway's user, outside the
> Docker sandbox, outside OpenClaw exec approvals, and outside `tools.*` policy. Containment is the
> harness's own CLI permission model, the selected `cwd`, and OS-level controls — nothing else. The
> `dev` agent's sandbox does not extend to it; `dev` cannot spawn ACP at all.

This sits alongside the existing MCP-servers-are-not-sandboxed gap. Both are cases where a component
runs with Gateway credentials outside the blast-radius model. ACP is the more dangerous of the two
because it is *designed* to write files and run commands.

---

## 7. The reverse direction: IDE → OpenClaw

`openclaw acp` is the mirror image of everything above. Instead of OpenClaw launching a harness, an
IDE launches **OpenClaw** and speaks ACP to it over stdio, forwarding prompts to the Gateway over
WebSocket.

| | `/acp spawn` (§1–6) | `openclaw acp` (this section) |
|---|---|---|
| Who starts whom | OpenClaw starts the harness | The IDE starts OpenClaw |
| Who does the coding | Claude Code / OpenCode | OpenClaw's own agent |
| Where you type | Discord, from your phone | VS Code / Zed, at your desk |
| Sandbox applies | No | Yes — normal agent policy |

Zed setup, in `~/.config/zed/settings.json`:

```json
{
  "agent_servers": {
    "OpenClaw ACP": {
      "type": "custom",
      "command": "openclaw",
      "args": ["acp", "--session", "agent:main:main"],
      "env": {}
    }
  }
}
```

Useful flags: `--url`, `--token-file`, `--session <key>`, `--session-label <label>`,
`--reset-session`, `--require-existing`, `--no-prefix-cwd`, `--provenance <off|meta|meta+receipt>`,
`-v`. Prefer `--token-file` over `--token` — command-line tokens are visible in process listings.

Debug without an IDE:

```bash
openclaw acp client
openclaw acp client --server-args --url ws://127.0.0.1:18789 --token-file ~/.openclaw/gateway.token
```

**When this is the better choice.** When you want your *assistant's* context — memory, calendar,
Notion, the accumulated knowledge of who you are — available while you code, rather than a
general-purpose coding harness that knows nothing about you. `openclaw acp` reaches a normal
OpenClaw agent session, so it inherits that agent's sandbox, tool policy, and approvals. It is the
contained option.

Honest limits, all documented upstream as Partial or Unsupported:

- Per-session `mcpServers` are **rejected** in bridge mode — configure MCP on the Gateway instead.
- Client filesystem (`fs/read_text_file`, `fs/write_text_file`) and terminal (`terminal/*`) methods
  are **unsupported**. The bridge does not drive your editor's filesystem or terminals.
- `loadSession` fully replays history only for bridge-created sessions; older sessions fall back to
  stored user/assistant text and do not reconstruct tool calls.
- `usage_update` is approximate and **carries no cost data**.
- Model selection and exec-host controls are not exposed as ACP config options.

So: `openclaw acp` is good for conversational, context-rich work in an editor. It is not a
replacement for Claude Code's editing loop. Use both, for different things.

There is also a third direction worth knowing exists: `openclaw mcp serve` lets Codex or Claude Code
connect as an external **MCP client** directly to existing OpenClaw channel conversations — neither
hosting a harness nor bridging a session. Use it when a coding agent needs to *pull context* from
your assistant without either side driving the other.

---

## 8. Model routing and cost

This project routes everything through OpenRouter with Kimi K2.6 primary, per
[Phase 2](../README.md#phase-2--model-routing-on-openrouter). **None of that applies to ACP
harnesses.**

An ACP harness owns its own model loop, its own model catalog, and its own provider auth. When you
run `/acp spawn claude`, no OpenRouter call happens. `models.aliases`, `agents.defaults.model`, the
tiered `smart` / `backup` / `cheap` routing — all bypassed entirely. Claude Code uses your Anthropic
subscription or API key. Gemini CLI uses your Google auth. OpenCode uses whatever provider you
configured *inside OpenCode*.

| | Embedded (`dev`) | ACP harness (`code`) |
|---|---|---|
| Model config | `agents.entries.dev.model` | The harness's own config |
| Auth | `OPENROUTER_API_KEY` | The harness's own credential store |
| Model refs | `openrouter/<provider>/<model>` | Harness-specific — **not portable** |
| Billed to | OpenRouter | The harness's provider |
| Visible to `openclaw gateway usage-cost` | Yes | **No** |

That last row is the one that will surprise you. OpenClaw's cost accounting tracks model calls
OpenClaw makes. It does not make the harness's calls, so it cannot see them. The weekly spend check
in [`operations.md` §1](./operations.md#1-operating-cadence) will report your ACP coding work as
costing approximately nothing, while your Anthropic bill does something else entirely. Track ACP
spend at the provider's own dashboard, and treat the two budgets as separate line items.

### Model overrides

`/acp model <id>` and `sessions_spawn({ model })` work only where the harness supports it:

- **Codex ACP:** OpenClaw normalises `openai/gpt-5.4` to the adapter model id, and slash forms like
  `openai/gpt-5.4/high` also set reasoning effort.
- **Everything else:** the harness must advertise ACP `models` *and* support `session/set_model`.
  If it does neither, OpenClaw **fails clearly** rather than silently falling back — you get
  `Cannot apply --model … did not advertise model support`, which is the correct behaviour.

**One genuine trap.** When `sessions_spawn({ runtime: "acp" })` omits `model`, it does not simply
let the harness use its default — it first applies `agents.defaults.subagents.model` or
`agents.entries.*.subagents.model` **if those are configured**. In this project those would be
OpenRouter refs like `openrouter/moonshotai/kimi-k2.6`, which are meaningless to Claude Code. The
result is a model-not-found error from the harness on a spawn where you never mentioned a model.

If you set a subagent model default, set it per-agent and keep it off the ACP agents:

```json5
{
  agents: {
    entries: {
      dev:  { subagents: { model: "openrouter/moonshotai/kimi-k2.6" } },
      code: { /* no subagents.model — let the harness use its own */ },
    },
  },
}
```

### Cost implications, briefly

Running Claude Code through ACP means paying Anthropic prices for your primary coding work rather
than Kimi-on-OpenRouter prices. That is not a defect — it is the reason to do it. The tiering logic
from Phase 2 still applies, one level up: use the expensive, capable harness for real feature work,
and keep `dev` on Kimi for the high-frequency mechanical jobs (PR triage digests, CI failure
summaries, commit message drafts). The cheapest ACP session is the one you didn't need to spawn.

`opencode` is the interesting middle: because you configure its provider yourself, it is the one
harness that can be pointed at your existing OpenRouter key — harness-quality agentic coding at
open-model prices. Worth testing early if cost matters.

---

## 9. Troubleshooting

| Symptom | Diagnosis | Fix |
|---|---|---|
| `ACP runtime backend is not configured` | Plugin missing, disabled, or blocked by `plugins.allow` | Install and enable `@openclaw/acpx`; include `acpx` in `plugins.allow` if that list is set; `/acp doctor` |
| `ACP is disabled by policy (acp.enabled=false)` | Globally off | `openclaw config set acp.enabled true` |
| `ACP dispatch is disabled by policy` | Automatic thread dispatch paused | Set `acp.dispatch.enabled true`. Explicit `sessions_spawn` still works regardless |
| `ACP agent "<id>" is not allowed by policy` | Not in the allowlist | Add to `acp.allowedAgents`, or use an allowed id |
| `/acp doctor` unhealthy right after a clean install | Probe agent defaults to `codex`, which you never installed | `openclaw config set plugins.entries.acpx.config.probeAgent claude`, restart |
| Harness command not found, but it works in Terminal | launchd Gateway doesn't inherit your shell `PATH` — classic with `nvm`/`mise`/`asdf` | Pin `plugins.entries.acpx.config.agents.<id>.command` to an absolute path ([§3.3](#33-per-harness-prerequisites)) |
| Harness command not found on first use | `npx` adapter fetch failed; no network or no npm | Pre-warm the adapter at the desk; check network; `/acp doctor` |
| `PermissionPromptUnavailableError: Permission prompt unavailable in non-interactive mode` | `permissionMode` blocks writes/exec and there's no TTY — **the most common ACP failure** | `permissionMode: "approve-all"` and restart. Read [§6.3](#63-permission-modes) first |
| Session fails early with little output | Same root cause, less obvious | Check Gateway logs for `AcpRuntimeError`. For graceful degradation instead of crashes, `nonInteractivePermissions: "deny"` |
| Vendor auth error from the harness | OpenClaw is fine; the CLI is not logged in | Authenticate that CLI on the Gateway host, as the Gateway's user |
| Model-not-found from the harness | Model ref valid elsewhere, not for this harness — or a `subagents.model` default leaking in | Use a model the harness lists, or clear `subagents.model` on ACP agents ([§8](#8-model-routing-and-cost)) |
| `Cannot apply --model … did not advertise model support` | Harness has no ACP `models` / `session/set_model` | Configure the model inside the harness itself |
| `Sandboxed sessions cannot spawn ACP sessions` | Requester is sandboxed; ACP is host-side | Expected. Spawn from the unsandboxed `code` agent, or use `runtime: "subagent"` |
| `sessions_spawn sandbox="require" is unsupported for runtime="acp"` | No such thing as a sandboxed ACP session | Use `runtime: "subagent"` for required sandboxing |
| `--bind here requires … an active conversation` | No bindable conversation in context | Move to the target channel and retry, or spawn unbound |
| `Conversation bindings are unavailable for <channel>` | Adapter lacks current-conversation binding | Use `--thread …` where supported, or configure `bindings[]` |
| `Thread bindings are unavailable for <channel>` | Adapter lacks thread binding | `--thread off`, or use Discord/Telegram |
| `Only <user-id> can rebind this …` | Someone else owns the binding | Rebind as owner, or use a different conversation |
| `Unable to resolve session target: …` | Bad key/id/label | `/acp sessions`, copy the exact key or label, retry |
| Session stalls after finishing work | Harness exited without reporting ACP completion | Update OpenClaw — current acpx reaps stale wrapper/adapter processes on close and startup |
| Harness sees `<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>` | Internal envelope leaked across the ACP boundary | Update OpenClaw. External harnesses should only ever receive plain completion prompts |
| Spawn ran in the wrong directory | `cwd` was inherited and the path didn't exist — missing paths fall back silently | Set `cwd` explicitly on every binding ([§4.4](#44-the-cwd-precedence-chain)) |
| `Command blocked by PreToolUse hook: Native hook relay unavailable` | **Not ACP.** This is the native Codex hook relay | `/new` or `/reset` in the bound Codex chat; if it recurs, restart the Codex app-server or the Gateway |

The general ladder, before blaming OpenClaw for any of it:

```text
/acp doctor                    # backend health and capabilities
/acp status                    # effective runtime options for this session
/acp sessions                  # what's actually running
```

Then, on the host: does the harness CLI run standalone, in that directory, as that user? If not, the
problem was never OpenClaw's.

---

## Sources

- [ACP agents](https://github.com/openclaw/openclaw/blob/main/docs/tools/acp-agents.md) — overview, runbook, bindings, delivery model, sandbox boundary
- [ACP agents — setup](https://github.com/openclaw/openclaw/blob/main/docs/tools/acp-agents-setup.md) — harness aliases, plugin config, MCP bridges, permission modes
- [`openclaw acp` (bridge mode)](https://github.com/openclaw/openclaw/blob/main/docs/cli/acp.md) — IDE integration, compatibility matrix, Zed setup
- [Agent runtimes](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-runtimes.md) — provider vs model vs runtime vs channel, Codex surfaces
- [Agent runtime architecture](https://github.com/openclaw/openclaw/blob/main/docs/agent-runtime-architecture.md) — code layout and runtime selection
- [Permission modes](https://github.com/openclaw/openclaw/blob/main/docs/tools/permission-modes.md) — host exec vs Codex Guardian vs ACPX harness permissions
- [Sub-agents](https://github.com/openclaw/openclaw/blob/main/docs/tools/subagents.md) — `sessions_spawn` parameters, ACP vs native delegation
- [Managed worktrees](https://github.com/openclaw/openclaw/blob/main/docs/concepts/managed-worktrees.md) — isolated checkouts, snapshots, cleanup
- [CLI backends](https://github.com/openclaw/openclaw/blob/main/docs/gateway/cli-backends.md) — the text-only local fallback path, distinct from ACP
- [Sandboxing](https://github.com/openclaw/openclaw/blob/main/docs/gateway/sandboxing.md) — what the sandbox does cover
- [Agent Client Protocol](https://agentclientprotocol.com/) — the protocol itself
- [OpenClaw docs map](https://github.com/openclaw/openclaw/blob/main/docs/docs_map.md)

Project cross-references: [`security.md`](./security.md) (threat model, injection test suite),
[`operations.md`](./operations.md) (cadence, cost model), [`README.md`](../README.md) (the plan).
