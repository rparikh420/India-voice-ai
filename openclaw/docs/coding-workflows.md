# Coding workflows — what you actually do with this thing

The [README](../README.md) plans a personal assistant that also does some dev. This document
assumes the opposite: **development is the primary use**, and everything else — inbox, calendar,
capture — is secondary load riding on the same Gateway.

This is the workflow document. It covers what you do day to day: kicking work off from your phone,
steering it mid-run, running four coding sessions against one repo without them fighting, getting
the agent to actually run the tests, and the cost and blast-radius decisions that follow.

**It is not the ACP reference.** ACP setup, harness support, per-harness config, permission modes,
and the ACP security model live in [`coding-agents.md`](coding-agents.md). Read that first if you
haven't wired ACP up yet. The one fact from it that shapes everything here:

> **ACP execution is not sandboxed by OpenClaw.** When OpenClaw spawns Claude Code or Codex through
> `@openclaw/acpx`, the harness runs as your user, on your filesystem, with your credentials, under
> *its own* permission model — not OpenClaw's `tools.exec` policy and not the Docker sandbox.

Every recommendation below is written with that in mind. If you have not read
[`coding-agents.md` §6](coding-agents.md), read it before you enable any of this.

Worked example throughout is the repo this directory lives in: `rparikh420/India-voice-ai`, a
Python LiveKit voice agent — `agent.py`, `orchestrator.py`, `tools.py`, `tests/`, `pytest.ini`,
`Dockerfile`, `docker-compose.yml`, and **no CI**. See
[`skills/deploy-runbook/SKILL.md`](../skills/deploy-runbook/SKILL.md) for its deploy path.

---

## Corrections to the plan, found while writing this

The starter config in this repo was written for an assistant-first setup. Four things in it are
actively wrong once coding is the primary workload. Fix them before anything below works.

| Where | Says | Actually |
|---|---|---|
| `openclaw.config.json5` | `tools.deny: [..., "sessions_spawn", "sessions_send"]` | **Deny wins over allow everywhere**, and it is global. With `sessions_spawn` in the global deny list, *no* agent can delegate — including `dev`. `tools.allow` on the agent cannot add it back. Delegation is the core of every parallel workflow here, so remove it from the global floor and deny it per-agent on `main` and `triage` instead. |
| `openclaw.config.json5` | `agents.entries.dev.sandbox.mode: "all"` | A sandboxed requester **cannot spawn ACP sessions at all.** `runtime: "acp"` is only advertised when "ACP is enabled, the requester is not sandboxed, and a backend plugin such as `acpx` is loaded." Sandboxed `dev` and ACP-driven coding are mutually exclusive as written. Pick one per agent — §1 splits them into two. |
| README Phase 7.3 | "Drive coding agents from chat: v2026.7.1 strengthened Codex and connected coding-agent workflows" | True but underspecified. The actual surface is three distinct mechanisms — native sub-agents (`sessions_spawn`), ACP harness sessions (`/acp spawn`, `sessions_spawn` with `runtime: "acp"`), and managed git worktrees. They have different isolation, different cost, and different failure modes. §1 has the decision table. |
| README Phase 7.3 | "CI autofix: GitHub webhook → hook endpoint" | The hook endpoint is real (`POST /hooks/agent`, `hooks.mappings`), but `gateway.bind: "loopback"` means **GitHub cannot reach it**. On a laptop host you poll from OpenClaw's side instead. §6 has the honest version. |

Also worth knowing before you read §4: **there are two unrelated files called `AGENTS.md`.**
OpenClaw's is a workspace bootstrap file injected into the system prompt. The one in your repo root
is read by the *harness* (Claude Code, Codex) from its working directory. `sessions_spawn`'s `cwd`
parameter is explicit about this — "native sub-agents still load bootstrap files from the target
agent workspace; `cwd` only changes where runtime tools and CLI harnesses do the delegated work."
Changing one does nothing to the other.

---

## 1. The shape of the thing

### Agents

Three agents, same trust split as the README, but `dev` gets divided. The reason is the sandbox
conflict in the corrections table: you cannot have one agent that is both contained and able to
drive an external harness.

```
┌───────────────────────────────────────────────────────────────────┐
│ main — "the assistant"                                            │
│   Discord/Slack front door. Memory on. Calendar, Notion.          │
│   exec: DENIED. Delegates everything real.                        │
│   Owns: what you're asked, what gets scheduled, what gets said.   │
├───────────────────────────────────────────────────────────────────┤
│ triage — "reads untrusted things"                                 │
│   Gmail + web. exec: DENIED. Cannot message out.                  │
│   Output is DATA. It never reaches an agent that can write code.  │
├───────────────────────────────────────────────────────────────────┤
│ dev — "contained work"                                            │
│   sandbox.mode: "all", scope: "session". exec allowed inside.     │
│   Reads repos, runs tests, greps, writes patches to a worktree.   │
│   NO ACP (sandboxed requesters can't spawn it). GitHub MCP read.  │
├───────────────────────────────────────────────────────────────────┤
│ harness — "drives external coding agents"                         │
│   NOT sandboxed, because ACP requires that. This is the agent     │
│   with real reach. Bound to #dev only. No email, no calendar,     │
│   no memory, no browsing, minimal MCP.                            │
└───────────────────────────────────────────────────────────────────┘
```

The `harness` agent is the one that can hurt you. Treat its config the way you'd treat an SSH key:
narrow, reviewed, and boring. It exists because ACP has to run unsandboxed, so the correct response
is to give the unsandboxed thing as little else as possible.

```json5
{
  agents: {
    entries: {
      dev: {
        workspace: "~/code",
        sandbox: { mode: "all", workspaceAccess: "rw", scope: "session" },
        tools: {
          profile: "coding",             // includes sessions_spawn / sessions_yield / subagents
          exec: { security: "allow" },   // safe only because of the sandbox above
          allow: ["github__*"],
          deny: ["message"],             // dev reports through announce, never speaks directly
          sandbox: { tools: { alsoAllow: ["github__*"] } },
        },
        subagents: {
          model: "openrouter/moonshotai/kimi-k2.5",   // children on the cheaper tier
          runTimeoutSeconds: 1800,                    // default is 0 = NO TIMEOUT. see §9.
          maxChildrenPerAgent: 4,
        },
        memory: { search: { rememberAcrossConversations: false } },
      },

      harness: {
        workspace: "~/code",
        sandbox: { mode: "off" },        // required: ACP is hidden to sandboxed requesters
        tools: {
          profile: "coding",
          exec: { security: "deny" },    // it does not need exec; the HARNESS does the executing
          allow: [],                     // no MCP servers at all
          deny: ["message", "browser", "cron", "gateway"],
        },
        memory: { search: { rememberAcrossConversations: false } },
      },
    },
  },

  // Global floor. NOTE the change from the starter config: sessions_spawn is gone from deny,
  // because deny wins everywhere and a global deny kills delegation for every agent.
  tools: {
    profile: "messaging",
    exec: { security: "deny", ask: "always" },
    elevated: { enabled: false },
    fs: { workspaceOnly: true },
    deny: ["group:automation", "group:runtime", "group:fs", "sessions_send"],
  },
}
```

Then deny delegation on the two agents that shouldn't have it:

```json5
agents: {
  entries: {
    main:   { tools: { deny: ["sessions_spawn"] } },   // main asks dev; it does not fan out itself
    triage: { tools: { deny: ["sessions_spawn"] } },   // untrusted input must never spawn anything
  },
}
```

`main` denying `sessions_spawn` is a judgement call, not a rule. If you want `main` to be the
single front door that fans work out, give it `sessions_spawn` with
`subagents.allowAgents: ["dev"]` so it can only ever spawn into the contained agent. What must not
happen is `triage` keeping it — see §10.

### Where does this piece of work go?

| Work | Runs on | Why |
|---|---|---|
| "What does `orchestrator.py` do?" | Embedded `dev`, main session | Read-only, one file, no fan-out. Delegation costs more than it saves. |
| "Grep the repo for every place we construct a `ChatOpenAI`" | Embedded `dev` | Same. Tool calls are cheap; the answer is short. |
| "Run the test suite and tell me what's red" | Embedded `dev`, sandboxed | Contained exec is exactly what the sandbox is for. No network needed. |
| "Summarise the last 20 commits for the weekly review" | `dev` **sub-agent**, `context: "isolated"` | Slow, self-contained, briefable in one paragraph. Keeps the main session's context small. |
| "Review this diff adversarially" | `dev` **sub-agent**, `context: "fork"` | Needs the transcript that produced the diff. One of the few genuine `fork` cases. |
| "Add retry logic to `booking_email.py`, with tests" | **ACP harness** on `harness`, own worktree | Multi-file edit + iterate + run tests. This is what harnesses are good at and sub-agents are mediocre at. |
| "Port `tools.py` mocks to a real HTTP backend" | **ACP harness**, own worktree, long-running | Days of work, many turns, needs to hold the whole shape in context. |
| Three independent bugfixes at once | **Three ACP sessions**, three worktrees | §3. This is where most of the value is. |
| "Every night, run the suite 20× and find flakes" | **Cron** → isolated `dev` turn | Scheduled, idempotent, no human in the loop. §8. |
| "Bump dependencies and see what breaks" | **Cron** → `dev` sub-agent in a worktree | Same, plus needs isolation because it mutates `requirements.txt`. |

Three rules that resolve most of the remaining cases:

1. **If it fits in one answer, don't delegate it.** A sub-agent costs a whole second context. The
   `delegationMode: "prefer"` setting exists, and for a coding-first setup it's tempting, but it
   makes the agent delegate things it should have just answered. Leave it on the default `suggest`
   until you've watched it for a week.
2. **If it writes to more than one file, give it a worktree.** Not because it will conflict today,
   but because you will want to run a second thing tomorrow.
3. **If it needs to iterate against test output, use a harness.** OpenClaw's native sub-agents can
   run `pytest`, but a harness is purpose-built for the edit → run → read failure → edit loop and
   will get there in fewer turns.

---

## 2. Driving code from chat

The core loop. You are on your phone, in `#dev`, and you want work to start.

### Kick it off

Plain language works — OpenClaw routes "run this as a one-shot Claude Code ACP session" and similar
phrasings to `runtime: "acp"` on its own. But the explicit form is worth learning, because it's the
one you can put in a cron job later:

```text
/acp spawn claude --mode persistent --thread auto --cwd /home/user/India-voice-ai --label booking-retry
```

| Flag | What it does |
|---|---|
| `--mode persistent\|oneshot` | `persistent` keeps the session alive for follow-ups; `oneshot` runs and closes |
| `--thread auto\|here\|off` | `auto` opens a Discord thread for this task's traffic |
| `--bind here\|off` | Makes *this* channel Codex/Claude-backed in place. Cannot combine with `--thread` |
| `--cwd <absolute-path>` | Working directory for the harness. **Always set this explicitly** |
| `--label <name>` | Operator-facing name in `/acp sessions`, session lists, and the task ledger |

Two habits worth forming from day one:

- **Always pass `--cwd`.** If you omit it, "ACP spawn inherits the target agent workspace when
  configured; missing inherited paths fall back to backend defaults." A backend default working
  directory is a coin flip, and a coding harness pointed at the wrong tree is the failure mode that
  wastes the most of your time.
- **Always pass `--label`.** In two hours you will have four sessions and no idea which is which.
  `/acp status`, `/acp sessions`, `openclaw tasks list`, and the Control UI all key off it.

`--thread auto` is what makes this survivable on Discord. Without it, one long coding run turns
`#dev` into a wall of progress messages. With it, the run gets its own thread and follow-ups in
that thread route straight back to the harness session — "bound follow-up messages go directly to
the ACP session until the binding is closed, unfocused, reset, or expired."

The same thing from an agent turn, which is what you use inside cron and skills:

```json
{
  "task": "In /home/user/India-voice-ai, add exponential-backoff retry to send_booking_confirmation_email in booking_email.py. Add a test in tests/ that proves the retry fires. Run `python -m pytest tests/ -v` and paste the real output. Do not touch .env.",
  "runtime": "acp",
  "agentId": "claude",
  "cwd": "/home/user/India-voice-ai",
  "label": "booking-retry",
  "thread": true,
  "mode": "session",
  "streamTo": "parent"
}
```

`streamTo: "parent"` relays progress summaries back to the requester session so you see movement
without opening the thread. Leave it off for jobs you don't want narrated.

### Watch it

Do not build a polling loop. This is stated as an operational rule in the sub-agent docs and it is
correct: "start child work once and wait for completion events instead of building poll loops
around `sessions_list`, `sessions_history`, `/subagents list`, or `exec` sleep commands."

Delivery is push-based. When work finishes, the task notifier delivers to the channel that started
it, or queues a system event that triggers an immediate heartbeat wake. You get told.

When you actively want to look:

```text
/acp status                 # backend, mode, state, runtime options, capabilities
/acp sessions               # recent ACP sessions from the store
/tasks                      # session-scoped task board in chat
```

```bash
openclaw tasks list --runtime acp --status running
openclaw tasks show <task-id|run-id|session-key>
openclaw sessions --agent harness --active 120
```

For native sub-agents the equivalents are `/subagents list`, `/subagents info <id>`, and
`/subagents log <id> [limit] [tools]` — add the literal `tools` token when you need to see the
tool calls, which are omitted by default.

### Steer it

This is the part that makes chat-driven coding actually work, and the part people don't discover.
You do not have to cancel and restart when a run goes sideways:

```text
/acp steer stop refactoring the LangGraph nodes, that's out of scope — just add the retry
/acp steer the test you added asserts on log text, assert on call count instead
/acp steer --session booking-retry run the full suite before you report back
```

Steering injects an instruction into a running session without replacing its context. Compare:

| Command | Effect |
|---|---|
| `/acp steer <text>` | Adds guidance. Run continues, context preserved. |
| `/acp cancel` | Aborts the **current turn**. Binding and session survive; you can prompt again. |
| `/acp close` | Ends the session and removes bindings. acpx reaps the wrapper/adapter process tree. |

Mid-run tuning without a restart:

```text
/acp set-mode plan                          # switch to planning mode
/acp permissions strict                     # tighten the harness approval profile
/acp model anthropic/claude-opus-4-6        # change model
/acp timeout 600
/acp cwd /home/user/India-voice-ai          # fix a wrong working directory
/acp reset-options                          # drop all session overrides
```

All of these require owner identity from an external channel, and `operator.admin` from internal
Gateway clients. Non-owners get `sessions`, `doctor`, `install`, and `help` only. That's the
control that stops a shared Slack channel from becoming a remote shell.

### Review the diff

Never take the summary as the artifact. The summary is a claim about the diff; the diff is the
diff. Ask for it in a form you can read on a phone:

```text
@bot in the booking-retry worktree: show me `git diff --stat` and then the full diff of
booking_email.py only. Don't summarise it, paste it.
```

Then, before you look at it yourself, spend fifty cents having something else look at it — §5.

### Approve or discard

Approve, for this repo:

```bash
# in the worktree
git diff --stat
python -m pytest tests/ -v          # you run it, not the agent. see §7.
git push -u origin openclaw/booking-retry
gh pr create --fill
```

Discard:

```text
/acp close
```

```bash
openclaw worktrees list
openclaw worktrees remove <id>
```

Removal is not destructive by default. "Removal first creates a synthetic commit containing tracked
and non-ignored untracked files, then pins it at `refs/openclaw/snapshots/<id>`." Snapshots stay
restorable for 30 days:

```bash
openclaw worktrees restore <id>
```

Restore rebuilds the branch at its original pre-snapshot commit and reapplies the differences as
unstaged modifications and untracked files — the synthetic commit never enters branch history. So
"discard" is reversible for a month, which is the right default for work you're not sure about at
11pm.

---

## 3. Parallel work

This is where the leverage is. One coding session driven from your phone is a novelty. Four
sessions working three bugfixes and a refactor against the same repo, none of them stepping on the
others, is a different kind of tool.

The thing that makes it work is that **OpenClaw has first-class managed git worktrees.** You do not
need to hand-roll `git worktree add` and a directory convention.

### The pattern

Each unit of work gets its own branch, its own checkout, and its own session. Nothing shares a
working tree.

```bash
openclaw worktrees create /home/user/India-voice-ai --name booking-retry
openclaw worktrees create /home/user/India-voice-ai --name flaky-tts-test --base-ref origin/main
openclaw worktrees create /home/user/India-voice-ai --name deps-bump
openclaw worktrees list --json
```

What you get:

| Property | Value |
|---|---|
| Location | `<openclaw-state-dir>/worktrees/<repo-fingerprint>/<name>` |
| Repo fingerprint | First 16 hex chars of SHA-256 over the canonical git common dir and origin URL |
| Branch | `openclaw/<name>` |
| Name constraint | Must match `[a-z0-9][a-z0-9-]{0,63}`; omit it and you get `wt-<8 hex>` |
| Base ref | Yours if given; otherwise fetch `origin`, use the remote default branch, fall back to local `HEAD` offline |

The checkouts live **outside** your repo. Nothing appears in `git status` at
`/home/user/India-voice-ai`, nothing needs `.gitignore` entries, and a `rm -rf` in the wrong place
doesn't take your source tree with it.

Then point one session at each:

```text
/acp spawn claude --thread auto --label booking-retry \
  --cwd ~/.openclaw/worktrees/<fingerprint>/booking-retry
```

Get the exact path from `openclaw worktrees list --json` — do not guess the fingerprint.

### The shortcut: spawn the worktree with the session

For work started by the agent rather than by you at a terminal, `sessions_spawn` provisions the
worktree itself:

```json
{
  "task": "Fix the flaky assertion in tests/test_tagged_speech.py. Run the suite 20 times to prove it. Report the pass count.",
  "label": "flaky-tts-test",
  "visible": true,
  "worktree": true,
  "worktreeName": "flaky-tts-test",
  "worktreeBaseRef": "origin/main",
  "cwd": "/home/user/India-voice-ai"
}
```

Constraints, in the order you'll hit them:

- `worktree: true` **requires** `visible: true`. `worktreeName` and `worktreeBaseRef` require both.
- `visible: true` creates a persistent dashboard session you can open in the Control UI. It
  supports `runtime: "subagent"` only — **not ACP.** For an ACP session in a worktree, create the
  worktree first (CLI or Control UI) and pass its path as `--cwd`.
- "A sandboxed target restricts `cwd` to that agent's workspace." Managed worktrees live under the
  OpenClaw state directory, not under `~/code`. So the sandboxed `dev` agent cannot be pointed at
  one this way. Worktree-based parallel work belongs to `harness`, or to `dev` with the sandbox off
  and approvals on.
- Visible spawning is rejected outright when the requester was itself spawned with an inherited
  tool allowlist or denylist. That restriction is fixed at spawn time and has no config override.
- Every explicit host path in `sessions.create` requires `operator.admin`. Ordinary worktree chat
  creation stays at `operator.write`.

### Making worktrees runnable for *this* repo

A fresh worktree of `India-voice-ai` cannot run anything, because `.env` is gitignored and the
agent starts and then fails on first room join without `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, `SARVAM_API_KEY`, `OPENAI_API_KEY`.

Two repo-local mechanisms fix this. Neither has a config key — they are contracts between the repo
and OpenClaw.

**`.worktreeinclude`** at the repo root copies selected ignored, untracked files into each new
worktree, using gitignore-pattern syntax:

```gitignore
# .worktreeinclude
.env
```

Only files git reports as *both* ignored and untracked are eligible. Existing destination files are
never overwritten, symlinked directories are not followed, and file modes are preserved.

**Understand exactly what you just did.** You now have a copy of your production LiveKit, Sarvam,
and OpenAI keys in every managed worktree under `~/.openclaw/worktrees/`. Three things make that
tolerable rather than reckless, and you should confirm all three:

1. `~/.openclaw` is already a secret store — FileVault on, 600/700 perms via
   `openclaw security audit --fix`. See [`security.md`](security.md) §5.
2. Snapshots do not leak it. "Ignored files never enter the repository object database." The `.env`
   copy is stored in chunked shared-state DB rows, not as a git object, so it never rides into a
   snapshot commit or a push.
3. It is scoped to what the tests need. For this repo, `pytest` needs `OPENAI_API_KEY` set (see §7)
   but no valid LiveKit or Sarvam credentials. If all you're doing is running tests in worktrees,
   ship a `.env.test` with dummies and put *that* in `.worktreeinclude` instead of the real one.
   Prefer this.

**`.openclaw/worktree-setup.sh`** runs on worktree creation, from inside the new worktree, with
`OPENCLAW_SOURCE_TREE_PATH` and `OPENCLAW_WORKTREE_PATH` in the environment. A nonzero exit aborts
creation and removes the new worktree and branch — which is what you want, because a half-provisioned
checkout is worse than none.

```bash
#!/usr/bin/env bash
# .openclaw/worktree-setup.sh — must be executable
set -euo pipefail

python -m venv .venv
./.venv/bin/pip install --quiet -r requirements.txt

# tests import langchain_openai at module scope; see docs/coding-workflows.md §7
if ! grep -q '^OPENAI_API_KEY=' .env 2>/dev/null; then
  echo 'OPENAI_API_KEY=sk-not-a-real-key-import-guard-only' >> .env
fi

echo "worktree ready: $OPENCLAW_WORKTREE_PATH (from $OPENCLAW_SOURCE_TREE_PATH)"
```

One caveat with real teeth: for **session** worktrees, `.worktreeinclude` provisioning applies to
every caller, but "repository checkout hooks and the `.openclaw/worktree-setup.sh` step run only
for `operator.admin` callers because they execute repository code." A worktree created by a
non-admin path gets the files but not the venv. If sessions come up with no `.venv`, that's why.

### Branch and label conventions

OpenClaw picks the branch name for you: `openclaw/<name>`. Your only lever is `<name>`, and it is
worth spending on, because that one string becomes the branch, the worktree directory, and — if you
reuse it — the session label and the `taskName`.

Use `<kind>-<subject>`:

| Name | Branch | Reads as |
|---|---|---|
| `fix-booking-retry` | `openclaw/fix-booking-retry` | a bugfix |
| `flaky-tts-test` | `openclaw/flaky-tts-test` | test stabilisation |
| `deps-bump-2026-07` | `openclaw/deps-bump-2026-07` | dated, so weekly runs don't collide |
| `spike-real-backend` | `openclaw/spike-real-backend` | throwaway; expect to discard |

Keep the same string across all four surfaces:

```text
/acp spawn claude --label fix-booking-retry --cwd <path-to>/fix-booking-retry
```

```json
{ "taskName": "fix_booking_retry", "label": "fix-booking-retry", "worktreeName": "fix-booking-retry" }
```

(`taskName` has a stricter charset — `[a-z][a-z0-9_-]{0,63}` — and cannot be `last` or `all`,
which are reserved control targets. Underscores are the easy way to stay legal.)

Target resolution accepts exact `taskName` matches and unambiguous prefixes, scoped to the active
and recent window. Two live children with the same handle make the target ambiguous and you fall
back to list index, session key, or run id. Dating the recurring ones avoids that entirely.

### Concurrency limits

| Setting | Default | Range | What it caps |
|---|---|---|---|
| `agents.defaults.subagents.maxConcurrent` | 8 | — | Global `subagent` lane concurrency |
| `agents.defaults.subagents.maxChildrenPerAgent` | 5 | 1–20 | Active children per session, at any depth |
| `agents.defaults.subagents.maxSpawnDepth` | 1 | 1–5 | Nesting. `2` enables the orchestrator pattern |

For coding work, 8 concurrent is too many — not because of the lane, but because eight coding
contexts is eight repo-sized token bills and eight Docker containers if `dev` is sandboxed with
`scope: "session"`. Start at 3.

```json5
agents: { defaults: { subagents: { maxConcurrent: 3, maxChildrenPerAgent: 3 } } }
```

Raise `maxSpawnDepth` to `2` only when you actually want main → orchestrator → workers. Depth-1
orchestrators then gain `sessions_spawn`, `subagents`, `sessions_list`, and `sessions_history`;
depth-2 workers are always leaves and can never spawn. Three levels of coding agents is three
levels of summarisation between you and the code, and it is usually a sign the task should have
been split by hand.

### Cleanup

Automatic, and conservative in the right direction:

- At run end, a worktree is removed **only** when `git status --porcelain` is empty *and*
  `git log HEAD --not --remotes --oneline` finds no unpushed commits. Otherwise it just releases
  the activity lock. Your unfinished work does not evaporate.
- Hourly cleanup snapshots and removes unlocked Workboard- and session-owned worktrees idle more
  than 7 days, even dirty ones. **Manual worktrees are never automatically removed** — the ones you
  make with `openclaw worktrees create` are yours to clean up.
- Snapshot records stay restorable for 30 days.

```bash
openclaw worktrees gc            # run idle, orphan, and retention cleanup now
openclaw worktrees list --json   # audit what's accumulated
```

That "manual worktrees are never automatically removed" line is the one that bites. Put
`openclaw worktrees gc` and a `worktrees list` review in the weekly dev cron (§8) or you will find
fourteen stale checkouts in three months.

---

## 4. Repo onboarding

Getting a repo ready before you point agents at it. Half an hour per repo, and it's the difference
between an agent that guesses your conventions and one that follows them.

### 4.1 `AGENTS.md` in the repo root

Read the corrections table again: this is **not** OpenClaw's workspace `AGENTS.md`. This is the
file the harness reads from its working directory. Claude Code also reads `CLAUDE.md`; the
practical move is to write `AGENTS.md` and symlink `CLAUDE.md` to it so you maintain one file.

```bash
cd /home/user/India-voice-ai
$EDITOR AGENTS.md
ln -s AGENTS.md CLAUDE.md
```

What earns its place in it, for this repo:

```markdown
# AGENTS.md — India-voice-ai

Python LiveKit voice agent. Gujarati clinic receptionist. Sarvam STT/TTS
(saaras:v3 / bulbul:v3), OpenAI gpt-4o for reasoning and tool calls, Silero VAD,
LiveKit BVC noise cancellation.

## Shape
- Flat layout. `agent.py` is at the repo root. There is no `src/` and no `research/`.
- It is a LiveKit **worker**, not a web service. It registers with LiveKit and waits
  for rooms. There is no port to curl and no health endpoint.
- `orchestrator.py` is a LangGraph flow used *from inside* tool execution for
  multi-step booking. It does not replace the LiveKit pipeline.
- `tools.py` is in-process mocks. No HTTP backend exists yet.

## Build and test
- `pip install -r requirements.txt` (heavy: pulls livekit-agents + langgraph)
- `python -m pytest tests/ -v` — `pytest.ini` sets `asyncio_mode = auto`
- `orchestrator.py` constructs `ChatOpenAI` at module scope (line 68), so test
  collection needs `OPENAI_API_KEY` set to any non-empty value. No API call is made
  at import.
- **There is no CI.** The local pytest run is the only gate. Do not assume a
  pipeline will catch anything.

## Never
- Read, print, echo, or paste `.env`. Not into chat, a PR, an issue, or a log.
- Commit `.env`, `*.pem`, or anything under `frontend/node_modules`.
- Push to `main`. Work on a branch; a human opens the PR.
- Fix a failing test by changing the assertion. Report it instead.

## Conventions
- `from __future__ import annotations` at the top of every module.
- structlog for logging; structured JSON at `LOG_LEVEL=INFO`. No bare `print`.
- Tests use plain `assert`. No unittest classes.
- Type hints on public functions. `Optional[X]`, not `X | None`, to match the
  existing 3.10 floor.

## Where things are
| Thing | File |
|---|---|
| Entrypoint, modes dev/console/start/download-files | `agent.py` |
| `<NoInterrupt>` / `<Mute>` tag parser | `tagged_speech.py` |
| Mock function tools | `tools.py` |
| LangGraph booking flow | `orchestrator.py` |
| Env and defaults | `config.py` |
| Deploy | `openclaw/skills/deploy-runbook/SKILL.md` |
```

Two things make this file work rather than decorate the repo:

- **Facts that contradict what the agent would assume.** "It is a worker, there is nothing to
  curl." "There is no CI." "`ChatOpenAI` is constructed at import." An agent will guess the
  conventions from the code; it cannot guess the things that aren't there.
- **A "Never" section.** Concrete prohibitions, not values. "Be careful with secrets" does nothing.
  "Do not read, print, or paste `.env`" is checkable.

Keep it under a page. Long context files get skimmed by models the same way they get skimmed by
people, and OpenClaw's own bootstrap injection truncates at `bootstrapMaxChars` (default 20000)
for exactly this reason.

### 4.2 A project skill

`AGENTS.md` is always-on context; a skill is on-demand procedure. Deploy steps, release process,
and debugging runbooks belong in a skill, because they're long and only relevant sometimes.

[`skills/deploy-runbook/SKILL.md`](../skills/deploy-runbook/SKILL.md) is the worked example and
already documents its own template at the bottom. The section ordering there — what this thing is /
preflight / test / build / roll out / verify / rollback / checklist / gates — is what makes a
runbook usable at 2am, and it's worth copying wholesale.

Three format traps, all covered in [README Phase 8](../README.md#phase-8--skills-teach-it-your-specifics)
and all worth repeating because each one fails silently:

- **`description` is load-bearing.** Only the skill list — name, description, location — enters the
  system prompt. If the description doesn't say *when to reach for it*, it never triggers. One
  line, under 160 characters.
- **`metadata` is not plain YAML.** It's parsed as YAML, flattened to a string, then re-parsed as
  JSON5. Write it in the JSON5 shape.
- **Skills cannot restrict their own tools.** No `allowed-tools` field — that's a Claude Code
  concept, not an OpenClaw one. Agent tool policy and the sandbox are the only enforcement.

Skills snapshot at session start. Restart the session to test a change; don't rely on the
mid-session refresh while you're iterating.

Keep the skill *in the repo it describes* — `openclaw/skills/deploy-runbook/` here — so it goes
stale visibly instead of silently.

### 4.3 Per-repo agent entries

Once you have more than one active repo, give each a `harness` entry rather than retargeting `cwd`
by hand every session:

```json5
agents: {
  entries: {
    "harness-voice": {
      workspace: "/home/user/India-voice-ai",
      repoRoot: "/home/user/India-voice-ai",   // shown in the system prompt's Runtime line
      sandbox: { mode: "off" },
      model: { primary: "openrouter/moonshotai/kimi-k2.6" },
      tools: { profile: "coding", exec: { security: "deny" }, allow: [], deny: ["message", "browser"] },
      skills: ["deploy-runbook"],
      memory: { search: { rememberAcrossConversations: false } },
    },
  },
},
bindings: [
  { channel: "discord", guild: "YOUR_SERVER_ID", channelId: "YOUR_DEV_CHANNEL_ID", agent: "harness-voice" },
  { channel: "discord", agent: "main" },
],
```

Three notes:

- **Bindings match most-specific-first.** List the channel-scoped rule before the channel-wide one,
  as in the starter config.
- **Never reuse `agentDir` across agents.** Doing so causes auth and session collisions. Give each
  agent its own state directory or let OpenClaw derive it from the id.
- **Per-agent `skills` allowlists replace the defaults, they do not merge.** Listing
  `["deploy-runbook"]` means that agent sees only that skill.

`repoRoot` is cosmetic-but-useful: it sets the repo path shown in the system prompt's Runtime line
instead of letting OpenClaw auto-detect by walking upward from the workspace. Set it when the
workspace is a subdirectory or a worktree.

### 4.4 What to pre-load, and what not to

| Pre-load | Leave out |
|---|---|
| `AGENTS.md` — shape, build/test, never-do, conventions | Anything the agent can read in three seconds with `git ls-files` |
| The one or two stale-docs warnings (`docs/07-deployment.md` still says `cd research`) | Full API docs for your dependencies |
| Where secrets live and that they are never to be read | Architecture essays |
| A project skill for deploy/release | Anything that changes weekly |

And one thing that catches people out with sub-agents specifically: **sub-agent context only
injects `AGENTS.md` and `TOOLS.md`.** Not `SOUL.md`, not `IDENTITY.md`, not `USER.md`, not
`MEMORY.md`, not `BOOTSTRAP.md`. If a coding convention only exists in `USER.md`, your sub-agents
have never seen it. Anything a delegated coding run needs has to be in `AGENTS.md`, in the repo's
`AGENTS.md`, or in the task text.

---

## 5. Review loops

The single highest-value thing in this document, and the cheapest. A review pass costs a fraction
of the run that produced the diff and catches a meaningful share of what you'd otherwise catch
yourself an hour later.

### The adversarial second pass

Do not ask the agent that wrote the code whether the code is good. It will say yes. Spawn a
separate reviewer with a hostile brief:

```json
{
  "task": "Review the diff on branch openclaw/fix-booking-retry in /home/user/India-voice-ai against origin/main. You did not write this and you are not trying to be agreeable.\n\nRun: git diff origin/main...openclaw/fix-booking-retry\n\nFor each of these, answer with file:line evidence or say 'none found':\n1. Behaviour changed that the diff does not claim to change.\n2. A test that would still pass if the implementation were deleted.\n3. An exception path that is swallowed, logged-and-continued, or bare-excepted.\n4. Anything that reads, logs, or serialises a value from config.py that came from an API key env var.\n5. Anything that assumes network availability inside a test.\n6. Retry logic without a bound.\n\nThen: run `python -m pytest tests/ -v` and paste the real terminal output. If you cannot run it, say so explicitly — do not describe what you expect it would print.\n\nEnd with exactly one line: SHIP or DO-NOT-SHIP, plus one sentence.",
  "taskName": "review_booking_retry",
  "label": "review: fix-booking-retry",
  "context": "isolated"
}
```

What makes this prompt work, transferably:

- **`context: "isolated"`.** A `fork` gives the reviewer the transcript in which the author already
  argued the code is fine. That is the opposite of what you want here. Isolated is both cheaper and
  more adversarial. (Forking is right for the *author's* follow-up work, not the reviewer's.)
- **Numbered checks, not "review this."** Open-ended review prompts produce prose. Numbered checks
  produce findings.
- **"file:line evidence or say 'none found'".** Forces a falsifiable claim per item.
- **A domain-specific check.** Item 4 is about *this* repo — `config.py` reads five API keys from
  the environment and `LOG_BOOKING_EMAIL_ADDRESSES` already exists because logging leaked patient
  emails once. Generic review checklists find generic problems.
- **"If you cannot run it, say so explicitly."** See §7.
- **A forced verdict.** One token you can route on. `SHIP` / `DO-NOT-SHIP` is greppable in a way
  that "looks reasonable overall" is not.

### Reviewing other people's diffs

Same shape, different fetch:

```text
@bot pull PR #42 in rparikh420/India-voice-ai, read the diff, and give me: what it changes in one
sentence, the three riskiest lines with file:line, and any question I should ask the author before
approving. Do not post anything to GitHub.
```

"Do not post anything to GitHub" belongs in the prompt every time until you have watched the
behaviour for weeks. GitHub MCP write scopes on an agent that reads PR bodies is a prompt-injection
path — a PR description is untrusted text, and it is being read by an agent that can comment,
label, and merge.

### When to trust it, and when not

| Trust it for | Do not trust it for |
|---|---|
| "You changed behaviour in `X` that the PR doesn't mention" | "This is correct" |
| "This test passes even if you delete the implementation" | "This is secure" |
| "This exception path is swallowed at `booking_email.py:88`" | "Performance is fine" |
| "You edited `.env.example` but not the runbook" | "This matches the architecture" |
| Mechanical consistency — naming, imports, error handling shape | Anything requiring knowledge of a system it cannot see |
| Test-quality smells | Whether the change is a good idea |

The pattern: **it is good at "this contradicts that" and bad at "this is right."** Contradiction is
checkable against text the model has. Correctness requires knowing what LiveKit does when a worker
registers twice, and it does not know that.

Two hard rules:

1. **A green review is not a substitute for running the tests yourself before you push.** It is a
   substitute for your *first* read, not your last.
2. **A review is not an approval.** OpenClaw's own PR automation puts this exactly right: "A
   positive ClawSweeper result is supporting evidence, not maintainer approval." Same here.

### Reviewing before a human ever sees it

The useful arrangement is a gate, not a filter:

```
agent writes code
  └─> adversarial review sub-agent
        ├─ DO-NOT-SHIP -> back to the author session with the findings, no human involved
        └─ SHIP        -> summary + diffstat + real test output to #dev, human decides
```

The point is not that the reviewer is right. It's that the loop before a human is cheap and the
loop after a human is expensive. Catching "you deleted the assertion" automatically means the diffs
that reach you are the ones worth your attention — which is the only way a coding assistant stays
worth reading in month three. The same reasoning as
[`skills/escalation-policy`](../skills/escalation-policy/SKILL.md), applied to diffs instead of
notifications.

---

## 6. CI and PR automation

### Start here: this repo has no CI

There is no `.github/` directory in `rparikh420/India-voice-ai`. The deploy runbook says so
plainly, and it means `python -m pytest tests/ -v` on someone's laptop is the only gate that
exists.

Every automation in this section is downstream of CI existing. So that's the prerequisite.

### The minimal workflow

```yaml
# .github/workflows/tests.yml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v5

      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"      # matches the Dockerfile base
          cache: pip

      - run: pip install -r requirements.txt

      - run: python -m pytest tests/ -v
        env:
          # orchestrator.py constructs ChatOpenAI at module scope (line 68), so test
          # COLLECTION fails without this. No API call is made at import — any
          # non-empty string works. Do NOT put a real key here.
          OPENAI_API_KEY: sk-not-a-real-key-import-guard-only
```

Four things to know before you commit it:

1. **The `OPENAI_API_KEY` line is not optional.** `orchestrator.py:68` is
   `_llm = ChatOpenAI(model="gpt-4o", temperature=0)` at module scope. `tests/test_orchestrator.py`
   imports from `orchestrator`, so collection constructs the client. The cleaner fix is to make
   `_llm` lazy; until someone does that, the workflow needs the dummy.
2. **The install is heavy.** `livekit-agents[sarvam,openai,silero,turn-detector]` plus `langgraph`,
   `langchain-openai`, and `lxml` (which needs a compiler). `cache: pip` matters here more than on
   a typical Python repo. Expect minutes, not seconds, on a cold cache.
3. **`pytest.ini` already sets `asyncio_mode = auto` and `testpaths = tests`**, so no extra pytest
   config is needed. `python -m pytest tests/ -v` is the same command the runbook uses — keep them
   identical so a CI failure reproduces locally with a copy-paste.
4. **It does not test the agent.** These are unit tests over `tools.py`, `orchestrator.py`, and
   `tagged_speech.py`. Nothing here proves a call connects. Green CI on this repo means "the pure
   functions still work," and the runbook's audio-in-Gujarati-out check remains the real gate.

Add branch protection on `main` requiring this check, or the workflow is decoration.

### Wiring CI results into OpenClaw

Now the honest part. `gateway.bind: "loopback"` means **GitHub cannot reach your Gateway.** The
hook endpoints are real:

```json5
{
  hooks: {
    enabled: true,
    path: "/hooks",
    token: "SET_VIA_CLI",                 // dedicated token, NOT gateway.auth.token
    allowedAgentIds: ["dev"],
    allowRequestSessionKey: false,
    mappings: { "ci-failure": { agent: "dev", sessionKey: "ci" } },
  },
}
```

```bash
curl -X POST http://127.0.0.1:18789/hooks/agent \
  -H 'Authorization: Bearer SECRET' \
  -H 'Content-Type: application/json' \
  -d '{"message":"CI failed on main. Diagnose.","agentId":"dev","name":"CI"}'
```

That works from your machine. It does not work from a GitHub Actions runner unless you expose the
Gateway, and the two ways to do that are both bad for a personal assistant:
`tailscale.mode: "funnel"` is public HTTPS and OpenClaw refuses to start it without password auth
for exactly that reason, and a reverse proxy is a new internet-facing service to maintain.

**On a laptop host, poll instead.** It is less elegant and strictly more robust: it works when the
Mac was asleep during the failure, which a webhook does not.

```bash
openclaw cron create "*/20 9-19 * * 1-5" \
  --name "CI watch" \
  --session isolated \
  --agent dev \
  --tz "America/New_York" \
  --announce --channel discord --to "$CH_DEV" \
  --message "Check GitHub Actions for rparikh420/India-voice-ai.

Report only runs that FAILED since your last check. For each: the workflow, the branch, the
failing step, and the actual error from the job log — the real assertion or traceback, not the
step name.

If it is a pytest failure, name the test and quote the assertion line.
If nothing failed, reply with exactly: NO_CHANGES"
```

Reserve `POST /hooks/agent` for local producers: a git `post-receive`, a local build script, a
`launchd` job. Those are on the same host, so loopback is not a limitation.

If you do migrate to a VPS ([`operations.md`](operations.md) §7), the webhook path becomes viable
and the security warnings become load-bearing: dedicated token, dedicated subpath (`/` is
rejected), `allowedAgentIds` set, `allowRequestSessionKey: false`, and if you must enable it,
`allowedSessionKeyPrefixes` to constrain the shapes.

### Autofix loops

The tempting version — CI fails, agent diagnoses, agent pushes a fix, CI runs again — is a loop
with no human in it and a `git push` at the end. Don't build that one.

The version worth building stops one step short:

```
CI fails
  └─> cron picks it up
        └─> dev diagnoses from the job log            (read-only)
              └─> harness spawns in a fresh worktree  (openclaw/ci-fix-<run-id>)
                    ├─ writes the fix
                    ├─ runs pytest locally, pastes real output
                    └─> posts diff + test output to #dev  ← STOPS HERE
                          └─> you read it and push
```

The last hop is yours. Concretely, in the harness prompt:

> Do not `git push`. Do not open a PR. Do not `git commit --amend` on anything that is already
> pushed. When you are done, print `git diff --stat`, the full diff, and the verbatim output of
> `python -m pytest tests/ -v`.

That is the same gate the deploy runbook already draws: preflight, tests, build, and reading logs
happen without asking; `git push`, opening or merging a PR, tagging a release, and touching
production need a human yes, every time.

### PR automation

The starter [`cron-jobs.sh`](../cron-jobs.sh) already installs a PR triage job on `dev` every 30
minutes. For a coding-first setup, extend the prompt to be about *your* PRs, not just review
requests:

```text
For each open PR I authored: is CI green, are there unresolved review comments, and has the
branch fallen behind main? For each PR awaiting my review: one-line summary and the single
riskiest change with file:line.
If nothing changed since your last run, reply with exactly: NO_CHANGES
```

Keep GitHub MCP scoped read-mostly on `dev`, the same way Gmail is scoped draft-never-send:

```bash
openclaw mcp configure github \
  --include 'get_*,list_*,search_*,pull_request_read,issue_read' \
  --exclude 'merge_*,delete_*,*_auto_merge,create_or_update_file,push_files'
```

Merging is a one-way door. Promote to autonomous merge only after weeks of watching, and even then
only on paths you've thought about specifically.

If you want a full review bot rather than a digest, OpenClaw's own is open source and designed to
be forked — [openclaw/clawsweeper](https://github.com/openclaw/clawsweeper). Note the boundary it
draws for itself: it avoids acting when "a trusted workflow would need to run untrusted contributor
code." Keep that boundary if you fork it.

---

## 7. Testing and verification

The failure mode this section exists to prevent: **an agent reports success it did not verify.**
It is the most common way a coding assistant wastes your time, and it is largely a prompting
problem, not a model problem.

### Make verification part of the task, not a follow-up

Bad, in a way that looks fine:

> Add exponential backoff to `send_booking_confirmation_email`.

You get a diff and "the change is complete and the tests should pass."

Good:

> Add exponential backoff to `send_booking_confirmation_email` in `booking_email.py`, max 3
> attempts.
>
> Then, in this order:
> 1. Run `python -m pytest tests/ -v` and paste the **full terminal output**, including the summary
>    line.
> 2. If any test fails, stop. Report the failing test name and the assertion verbatim. Do not fix
>    the test.
> 3. If you could not run pytest at all — missing dependency, missing env var, wrong directory —
>    say exactly that and what the error was. Do not describe what you expect the output would be.
>
> Your final message must contain the real pytest summary line. If it does not, you have not
> finished.

Four things are doing the work:

- **The exact command.** `python -m pytest tests/ -v`, matching the runbook and matching CI.
- **"Paste the full terminal output."** Output is evidence. A summary of output is a claim.
- **"Do not fix the test."** Otherwise a failing assertion becomes a passing assertion. This is the
  single most important line in the whole prompt.
- **An explicit path for "I couldn't run it."** Without one, a model with no working pytest will
  produce a plausible-looking pass. Given one, it usually takes it.

Encode this once in the repo's `AGENTS.md` rather than retyping it — the "Never" section in §4.1
already has "fix a failing test by changing the assertion."

### For this repo specifically

```bash
cd /home/user/India-voice-ai
python -m pytest tests/ -v
```

| Test file | Covers |
|---|---|
| `tests/test_tagged_speech.py` | The `<NoInterrupt>` / `<Mute>` parser in `tagged_speech.py` |
| `tests/test_tools.py` | Mock tools in `tools.py` (imports `livekit.agents`) |
| `tests/test_orchestrator.py` | LangGraph flow in `orchestrator.py` (imports `langchain_openai`) |

Environment facts that determine whether the run happens at all:

- `pytest.ini` sets `asyncio_mode = auto` and `testpaths = tests`. Bare `pytest` works; the runbook
  uses the explicit path, so match it.
- **`OPENAI_API_KEY` must be set to something non-empty** or `tests/test_orchestrator.py` fails at
  collection, because `orchestrator.py:68` constructs `ChatOpenAI` at module scope. No network call
  is made at import. A dummy value is correct and a real key is a needless exposure.
- No LiveKit or Sarvam credentials are needed. The tests do not connect to anything.
- If `dev` is sandboxed, remember Docker sandboxes default to `network: "none"`. Tests run fine
  because they don't use the network — but `pip install -r requirements.txt` will not, so the image
  or the worktree setup script has to have installed the deps already.

### The "report failures faithfully" discipline

Three prompt fragments that carry most of the weight. Put them in `AGENTS.md`, then stop typing
them:

| Fragment | Prevents |
|---|---|
| "Paste the verbatim terminal output, not a summary" | Confident paraphrase of a run that didn't happen |
| "If a test fails, stop and report it. Do not modify the test." | Assertion-weakening |
| "If you could not run the command, say so and give the error." | Hallucinated green |

And on your side: **treat any report without pasted output as unverified.** Not as a lie — as
unverified. That's a habit, not a config setting, and it's the one that matters most.

### Beyond unit tests

For this repo, unit tests are the cheap gate and are nowhere near the whole story. The deploy
runbook is explicit: "A deploy is not verified until audio has gone in and Gujarati has come out."

An agent can get you most of the way:

```text
@bot in the booking-retry worktree: run `docker compose up -d --build`, then
`docker logs -f --tail 100 voice-agent` for 60 seconds. Tell me whether the worker registration
line to the LiveKit URL appears. Quote it. Then `docker compose down`.
```

That catches the runbook's named deceptive failure — a container that stays up, looks healthy to
Docker, never registers, and serves nobody.

The browser leg is also automatable, since `serve_dev_ui.py` serves the dev UI on
`http://127.0.0.1:8765`:

```bash
openclaw browser --browser-profile openclaw doctor
openclaw browser --browser-profile openclaw start
openclaw browser --browser-profile openclaw open http://127.0.0.1:8765
openclaw browser --browser-profile openclaw screenshot --full-page
openclaw browser extract "Did the call connect, and is there a transcript visible?"
```

`extract` answers a question against the page using the agent model and returns only the wrapped
answer, reporting `NOT_FOUND` when the answer isn't there. Useful for "did the UI reach the
connected state" without dumping the DOM into context.

What no agent can do for you is listen. Somebody has to hear Gujarati come out of the speaker.

---

## 8. Long-running and background work

### The mental model

Three layers, and conflating them is the usual source of confusion:

| Layer | What it is | Command |
|---|---|---|
| **Cron** | The scheduler. Jobs in SQLite, survives restarts. | `openclaw cron` |
| **Tasks** | The activity ledger. Records what detached work happened and whether it worked. | `openclaw tasks` |
| **Sessions** | The conversation context the work runs in. | `openclaw sessions` |

Tasks "do **not** replace sessions, cron jobs, or heartbeats — they are the **activity ledger** that
records what detached work happened, when, and whether it succeeded." ACP runs, sub-agent spawns,
every cron execution, and CLI operations all create task records. Heartbeat turns and normal chat
do not.

```bash
openclaw tasks list --runtime acp --status running
openclaw tasks list --runtime subagent --status failed
openclaw tasks show <lookup>              # task id, run id, or session key
openclaw tasks cancel <lookup>
openclaw tasks audit                      # stale, lost, delivery-failed, inconsistent
openclaw tasks maintenance --apply        # reconcile, stamp cleanup, prune
```

`openclaw tasks audit` is the one to actually run. Codes include `stale_queued`, `stale_running`,
`lost`, `delivery_failed`, `missing_cleanup`, and `inconsistent_timestamps`. A recurring
`stale_running` on coding tasks means runs are wedging — usually because
`subagents.runTimeoutSeconds` is still at its `0` default (§9).

### Not becoming noise

Every proactive path independently decides whether to speak, and the failure mode is silent: nobody
reports a slightly noisy assistant, they just stop reading the channel. Read
[`skills/escalation-policy`](../skills/escalation-policy/SKILL.md) before you add a single job. Its
default is **Queue**, and coding automation should almost always queue.

The dev-specific mapping:

| Event | Outcome | Where |
|---|---|---|
| Production voice agent down, calls dropping | **Interrupt** | DM |
| Secret detected in a diff or a log | **Interrupt** | DM |
| CI red on `main` | **Queue** | `#dev` |
| Coding task finished, diff ready | **Queue** | `#dev` thread |
| Dependency update available | **Queue** | weekly digest |
| Flaky test found | **Queue** | `#dev` |
| Coding task finished, nothing changed | **Silent** | nowhere |
| Nightly job found nothing | **Silent** | `NO_CHANGES` |

Two mechanical levers that make queue-by-default actually work:

**Sentinel tokens.** Every job below ends with "if nothing changed, reply with exactly:
`NO_CHANGES`". Cron suppresses `NO_REPLY` runs by design, and a consistent sentinel makes a quiet
job genuinely quiet. Sub-agents have their own: replying exactly `ANNOUNCE_SKIP` posts nothing.

**Notification policy per task.**

```bash
openclaw tasks notify <lookup> silent          # nothing
openclaw tasks notify <lookup> done_only       # terminal state only (default)
openclaw tasks notify <lookup> state_changes   # every transition — debugging only
```

`state_changes` on a long coding run is a firehose. Use it when a run is misbehaving, then put it
back.

### Starter dev cron jobs

Consistent with [`cron-jobs.sh`](../cron-jobs.sh). Note the flags: **`--announce --channel discord
--to "channel:<id>"`**, not `--deliver`/`--target` (`--deliver` survives as a deprecated alias for
`--announce`; `--target` is not a flag at all — see [`operations.md`](operations.md) corrections).

Save as `openclaw/cron-jobs-dev.sh` and run it separately, so you can add dev automation without
reinstalling the personal set.

```bash
#!/usr/bin/env bash
# Development-focused automation. Companion to cron-jobs.sh.
#
#   bash openclaw/cron-jobs-dev.sh
#
# Every job runs ISOLATED (~2-5K tokens/run instead of ~100K) and is idempotent:
# the gateway sleeps when the laptop sleeps, so jobs summarise "what I missed"
# rather than assuming they fired on the dot.
#
# Review each job before running. Comment out what you don't want.
set -euo pipefail

TZ_NAME="America/New_York"
CH_DEV="channel:YOUR_DEV_CHANNEL_ID"
REPO="/home/user/India-voice-ai"

if [[ "$CH_DEV" == *YOUR_* ]]; then
  echo "Edit CH_DEV at the top of this script first." >&2
  exit 1
fi

new_job() { echo; echo "→ $1"; }

# ── Nightly test gate — 02:00 daily ─────────────────────────────────────────
# No CI exists (see docs/coding-workflows.md §6). This is the closest thing.
new_job "Nightly test gate"
openclaw cron create "0 2 * * *" \
  --name "Nightly test gate" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --tools exec,read \
  --announce --channel discord --to "$CH_DEV" \
  --message "Run the test suite on main for $REPO.

  cd $REPO && git fetch origin && git log --oneline -1 origin/main
  python -m pytest tests/ -v

OPENAI_API_KEY must be set to any non-empty value or collection fails at
orchestrator.py:68 — that is an environment problem, not a test failure. Report it
as such if you hit it.

If everything passes, reply with exactly: NO_CHANGES
If anything fails, report the failing test names and the verbatim assertion output.
Do not modify any test. Do not push anything."

# ── Flaky test hunt — Wed 03:00 ─────────────────────────────────────────────
new_job "Flaky test hunt"
openclaw cron create "0 3 * * 3" \
  --name "Flaky test hunt" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --tools exec,read \
  --announce --channel discord --to "$CH_DEV" \
  --message "Hunt for flaky tests in $REPO.

Run the suite 20 times: for i in \$(seq 20); do python -m pytest tests/ -q; done
Count failures per test across runs.

Report ONLY tests that failed at least once but not every time — those are flakes.
A test that fails all 20 times is broken, not flaky; say so separately in one line.
For each flake, quote the assertion and name the file:line.

If every run was identical, reply with exactly: NO_CHANGES"

# ── Dependency drift — Mon 08:30 ────────────────────────────────────────────
new_job "Dependency drift"
openclaw cron create "30 8 * * 1" \
  --name "Dependency drift" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --tools exec,read \
  --announce --channel discord --to "$CH_DEV" \
  --message "Check dependency drift for $REPO.

Compare requirements.txt against current releases (pip index / pip list --outdated).
The pins that matter: livekit-agents~=1.3, livekit-plugins-noise-cancellation~=0.2,
langgraph, langchain-openai, lxml.

Report ONLY: (a) anything with a published CVE, (b) major-version bumps available on
the two livekit packages, (c) anything whose new version would break the ~= pin.
One line each with current -> available.

Do NOT edit requirements.txt. Do NOT install anything. Report only.
If nothing qualifies, reply with exactly: NO_CHANGES"

# ── Doc drift — Fri 16:00 ───────────────────────────────────────────────────
# Deploy docs going stale is how a 2am rollback fails. This already happened once:
# docs/07-deployment.md still says `cd research` after the repo was flattened.
new_job "Doc drift"
openclaw cron create "0 16 * * 5" \
  --name "Doc drift" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --tools read \
  --announce --channel discord --to "$CH_DEV" \
  --message "Check documentation against reality in $REPO.

Compare openclaw/skills/deploy-runbook/SKILL.md, README.md, AGENTS.md, and docs/
against the actual repo:
1. Paths that no longer exist (the repo was flattened; docs/07-deployment.md still
   says 'cd research').
2. Commands that would fail as written.
3. Env vars in .env.example that config.py does not read, or vice versa.
4. Anything in the runbook contradicted by Dockerfile or docker-compose.yml.

Report file:line and the specific mismatch. Do not edit anything.
If the docs match the code, reply with exactly: NO_CHANGES"

# ── Worktree + task hygiene — Sun 18:00 ─────────────────────────────────────
# Manual worktrees are NEVER auto-removed. Without this they accumulate forever.
new_job "Worktree and task hygiene"
openclaw cron create "0 18 * * 0" \
  --name "Worktree and task hygiene" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --tools exec,read \
  --announce --channel discord --to "$CH_DEV" \
  --message "Weekly dev hygiene. Report findings; apply nothing.

1. openclaw worktrees list --json — flag any worktree older than 14 days, and say
   for each whether it is dirty or has unpushed commits (those are NOT safe to drop).
2. openclaw tasks audit — report warn and error findings. Repeated stale_running on
   coding tasks means runs are wedging; check subagents.runTimeoutSeconds.
3. openclaw gateway usage-cost --days 7 --all-agents — call out any agent whose
   spend more than doubled week over week.
4. Local branches merged into main that still exist.

Recommend a specific cleanup command for each item. Do not run any of them."

# ── CI watch — every 20m on weekdays ────────────────────────────────────────
# Only useful once .github/workflows/tests.yml exists. See §6.
new_job "CI watch"
openclaw cron create "*/20 9-19 * * 1-5" \
  --name "CI watch" \
  --session isolated \
  --agent dev \
  --tz "$TZ_NAME" \
  --announce --channel discord --to "$CH_DEV" \
  --message "Check GitHub Actions for rparikh420/India-voice-ai.

Report only runs that FAILED since your last check. For each: workflow, branch,
failing step, and the actual error from the job log — the real assertion or
traceback, not the step name. For pytest failures, name the test and quote the
assertion.

If nothing failed, reply with exactly: NO_CHANGES"

echo
echo "Installed. Verify with:"
echo "  openclaw cron list"
echo "  openclaw cron run <jobId> --wait      # fire one manually to test"
echo
echo "Add failure alerts to the jobs you care about:"
echo "  openclaw cron edit <jobId> --failure-alert --failure-alert-after 2 \\"
echo "    --failure-alert-channel discord --failure-alert-to \"$CH_DEV\""
echo
echo "Give it two weeks, then delete every job whose output you've started skimming."
```

Notes on choices in there:

- **`--tools exec,read`** stores an explicit tool policy on the job. New jobs that can run tools
  always store one; created by an operator without `--tools`, they store an unrestricted `*`. A
  scheduled job that only needs to run pytest and read files should say so.
- **`--agent dev`** on all of them. These are contained, read-mostly jobs. None spawns an ACP
  harness, because a scheduled unattended harness run is an unsandboxed process editing your repo
  while you sleep. If you want that, do it deliberately, in a worktree, with an explicit
  no-push instruction — not as a default.
- **Nothing writes.** Every job reports and recommends. The moment a cron job starts editing
  `requirements.txt`, you have unattended commits from a scheduler you check weekly.
- **Times are staggered** across the week so `usage-cost` stays legible per job.
- **`--failure-alert`** is not on by default because a job that fails once during a sleep gap
  should not page you. Add it to the ones you'd actually want to know about.

### A note on `cron.triggers.enabled`

Leave it **off**, which is the default. Enabling it permits headless script execution with the
owning agent's full tool policy, including `exec`. In a coding-first setup, the owning agent is
`dev` — the one with `exec` allowed. Unattended shell commands with your permissions, triggered by
conditions a model evaluates, is not a thing you want on by default.

---

## 9. Cost control for coding work

Coding sessions are the expensive ones. [`operations.md` §3](operations.md#3-cost-model) models the
assistant workload at roughly $35/month with an honest $25–60 band. Coding work does not fit that
model, and the reasons are structural.

This section is about *workflow* cost — turn count, fan-out, context growth, wedged runs. For which
model to route a harness at and how ACP model selection resolves, see
[`coding-agents.md` §8](coding-agents.md).

### Where the money actually goes

| Driver | Why it's different from assistant work | Rough shape |
|---|---|---|
| **Repo context** | Every turn re-sends accumulated context. Read six files and the seventh tool call carries all six. | An assistant turn is ~25K in. A coding turn that has read a dozen files is several times that, and it *grows within the session*. |
| **Turn count** | Edit → run tests → read failure → edit is 4 turns per attempt, and attempts 2–4 are normal. | 10–30 turns on a real task vs 1–3 for "what's on my calendar." |
| **Fan-out** | `maxConcurrent: 8` × a coding-sized context each. | The multiplier that turns a $2 task into a $16 one. |
| **`context: "fork"`** | Copies the requester's whole transcript into the child before it starts. | Doubles the base cost of every child that uses it. |
| **No timeout** | `subagents.runTimeoutSeconds` defaults to `0` — **no timeout**. | A wedged run burns tokens until you notice. This is the one that produces a surprise bill. |

### The levers, ranked

| # | Lever | Config / command | Cost of using it |
|---|---|---|---|
| 1 | **Set a run timeout.** The default is unbounded. | `agents.defaults.subagents.runTimeoutSeconds: 1800` | A genuinely long task gets cut off. Raise it per agent, don't leave it at 0. |
| 2 | **Cheaper model for children.** | `agents.defaults.subagents.model: "openrouter/moonshotai/kimi-k2.5"` | Weaker on long tool chains. Fine for review and summarisation; watch it on implementation. |
| 3 | **Drop `maxConcurrent` to 3.** | `agents.defaults.subagents.maxConcurrent: 3` | Less parallelism. Three is already a lot of diffs to read. |
| 4 | **`isolated`, not `fork`, by default.** | `context` param on `sessions_spawn` | You have to write a better task prompt. Do that anyway. |
| 5 | **Scope tools on scheduled jobs.** | `openclaw cron edit <id> --tools exec,read` | Fewer tools, fewer speculative calls, smaller context. |
| 6 | **Pin expensive jobs down a tier.** | `openclaw cron edit <id> --model <cheap-ref>` | Per-job, no agent change. Same lever as `operations.md` §3. |
| 7 | **`--light-context` on jobs that don't need bootstrap files.** | `--light-context` | The job loses `AGENTS.md`. Only for jobs whose prompt is fully self-contained. |
| 8 | **Close finished ACP sessions.** | `/acp close` | Idle runtime workers are cleaned up eventually, but persistent sessions with a live binding are preserved indefinitely. |

Lever 1 is the whole game here, the way `isolatedSession` is for heartbeats. Everything else is
proportional to how much you use the system; an unbounded wedged run is unbounded.

### Watching it

```bash
openclaw gateway usage-cost --days 7 --all-agents
openclaw gateway usage-cost --days 7 --agent harness --json
openclaw gateway usage-cost --days 30 --agent dev
```

**One important gap.** `usage-cost` accounts for model calls OpenClaw makes. An ACP harness
authenticating with its own credentials — a Claude Code subscription, a Codex login — bills on
*that* account, and OpenClaw does not see it. The `sessions_spawn` docs are explicit that when no
model is configured, ACP spawns "let the ACP harness use its own default model." So a
`usage-cost` figure of near-zero for the `harness` agent is not evidence that ACP work is free; it
is evidence that the spend is somewhere else. Run one real ACP task, then check both OpenClaw's
number and the harness provider's dashboard, before you trust either.

### A ceiling you set on purpose

Because two of the three cost drivers scale with things you don't watch minute to minute — turn
count and context growth — set a hard limit outside the system:

- A spend cap on the OpenRouter key.
- A separate OpenRouter key for the `harness` and `dev` agents, with its own cap, so a runaway
  coding session cannot consume the budget that runs your morning brief.
- The weekly hygiene job in §8 flags any agent whose spend more than doubled.

Also still true from `operations.md`: `HEARTBEAT_OK` and `target: "none"` suppress the *message*,
not the run. Quiet is not cheap. The only way a job costs nothing is not existing.

---

## 10. Anti-patterns

Blunt list. Each of these is something that looks like a productivity win and is actually a way to
lose a weekend or a credential.

### Unattended `approve-all` on a repo with credentials

```bash
# Do not do this and then walk away.
openclaw exec-policy preset yolo
```

```json5
// Equivalently, in the host approvals file:
{ version: 1, defaults: { security: "full", ask: "off", askFallback: "full" } }
```

`India-voice-ai` has a `.env` with LiveKit, Sarvam, OpenAI, and Resend keys, and this machine has
your SSH keys and `~/.openclaw/` — which is itself a secret store holding channel tokens, provider
credentials, and every transcript. Never-prompt exec plus an unsandboxed harness plus overnight is
a configuration where a single confused loop can exfiltrate all of it, and you will find out from
the logs.

The defensible unattended configurations are: sandboxed with `exec: allow` inside the container, or
unsandboxed with approvals on. Unsandboxed, never-prompt, unattended is not on the list.

```bash
openclaw approvals get              # know what your effective policy actually is
openclaw approvals pending          # the queue that "ask: off" makes disappear
```

### Letting `triage` output trigger code changes

The single worst thing you can build with these pieces:

```
GitHub issue / email / PR comment
  └─> triage summarises it
        └─> main reads the summary
              └─> spawns a harness to "fix" it
                    └─> unsandboxed write access to your repo
```

That is a remote code execution path with extra steps, and it is exactly the pattern the
three-agent split exists to prevent. `triage` output is **data**. `README` says it: "content that
arrives from outside never reaches an agent that can execute."

Three concrete guards, all cheap:

1. `triage` keeps `sessions_spawn` denied. Untrusted-content readers never spawn.
2. Any path from external content to a code change has a human in it. Not an approval prompt you
   click through — a read.
3. GitHub MCP stays read-mostly on the agent that reads PR bodies and issue text (§6). A PR
   description is attacker-controlled text.

`triage` also runs the cheapest model in the config on the most hostile input, deliberately, with a
documented condition attached ([`security.md`](security.md) §7): widen its tools and you upgrade
its model in the same commit. Giving it any coding reach is the widening that condition is about.

### Agents pushing to `main`

Put it in `AGENTS.md`, put it in the task prompt, and then enforce it where it actually counts:

```bash
gh api -X PUT repos/rparikh420/India-voice-ai/branches/main/protection \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -F "enforce_admins=true"
```

Prompts are guidance. Branch protection is enforcement. Managed worktrees already push you the
right way — every branch is `openclaw/<name>` and none of them is `main` — but a harness with your
git credentials can still `git push origin HEAD:main` if nothing stops it.

Same logic for `git push --force`, `git commit --amend` on pushed commits, and rewriting history in
a shared branch. None of those should be in an unattended prompt.

### Accepting green-looking output without running anything

"All tests pass" is a claim. A pasted pytest summary line is evidence. A pasted summary line from a
run you can reproduce is proof.

If a report doesn't contain output, treat the work as unverified and ask for the output. If the
answer is "I don't have pytest available," that is the actual state of the world and it is more
useful than a confident pass. §7 has the prompt shape that produces this reliably.

The reason this matters more here than on most repos: there is no CI. Nothing downstream catches
it.

### One giant session for everything

A single long-lived coding session accumulates context until every turn is expensive and the model
is reasoning over three unrelated tasks. Symptoms: rising per-turn cost, `Largest session files:`
naming a huge transcript in the stability bundle, and the agent confusing two tasks' details.

One session per unit of work, one worktree per session. When a task is done, `/acp close`.

### `context: "fork"` as the default

Fork copies the requester's whole transcript into the child. It exists for genuinely
context-dependent delegation — most usefully, the author's own follow-up work. Using it because
"more context is better" doubles your cost and, for review specifically, actively harms the result
by handing the reviewer the argument that the code is fine.

The docs are direct: "Use `fork` sparingly. It is for context-sensitive delegation, not a
replacement for writing a clear task prompt."

### Polling loops instead of `sessions_yield`

```text
# No.
spawn → sleep 30 → /subagents list → sleep 30 → /subagents list → ...
```

Every poll is a model turn you pay for, and the completion is push-based anyway. After spawning
required work, call `sessions_yield` — it ends the turn and lets the completion arrive as the next
message. When active children exist, OpenClaw injects an `Active Subagents` block into normal turns
showing session keys, run ids, statuses, labels, and `taskName` aliases, so the status is in front
of you without asking.

Only exception: `sessions_yield` isn't in every tool profile. If it isn't in your effective list
(`/tools` will tell you), do not invent a polling loop as a substitute.

### `.worktreeinclude` with real production secrets

Convenient, and it puts your live keys in N checkouts. §3 has the safer version: a `.env.test`
with dummy values. For this repo the tests need `OPENAI_API_KEY` present but never valid, and need
nothing from LiveKit or Sarvam at all. Copy the dummy.

If you do include the real `.env`, at least know the shape of the exposure: it does not enter git
objects and does not ride into snapshots, but it does sit in cleartext under `~/.openclaw/` in as
many copies as you have worktrees, and it is in whatever backups cover that directory.

### `tools.elevated.enabled: true`

Elevated exec runs **outside** the sandbox — it punches straight through containment. There is
almost never a coding workflow that needs it, and the ones that seem to (installing a system
package, touching Docker) are better solved by fixing the image or the worktree setup script.

Same family: `sandbox.mode: "off"` plus `exec: { security: "allow" }` plus no approvals. That's
unsandboxed shell with no gate, and it is not a defensible setup on a machine that has your SSH
keys.

### `--cwd` at `$HOME`, or omitted

```text
/acp spawn claude --cwd /Users/you          # no
/acp spawn claude                           # also no — it falls back to backend defaults
```

ACP is unsandboxed. `--cwd` is the main thing scoping where it works. Point it at a repo, ideally a
worktree, and never at a directory containing `.ssh`, `.aws`, `.openclaw`, or your whole home
directory.

### Trusting a review as an approval

Covered in §5 and worth restating as the last item, because it's the one that produces the worst
outcomes: a positive automated review is supporting evidence, not sign-off. It is very good at
"this contradicts that." It has no idea whether the change is a good idea.

---

## Sources

- [Sub-agents](https://docs.openclaw.ai/tools/subagents)
- [ACP agents](https://docs.openclaw.ai/tools/acp-agents)
- [ACP agents — setup](https://docs.openclaw.ai/tools/acp-agents-setup)
- [`openclaw acp` CLI](https://docs.openclaw.ai/cli/acp)
- [Managed worktrees](https://docs.openclaw.ai/concepts/managed-worktrees)
- [Background tasks](https://docs.openclaw.ai/automation/tasks)
- [`openclaw tasks` CLI](https://docs.openclaw.ai/cli/tasks)
- [Task Flow](https://docs.openclaw.ai/automation/taskflow)
- [Cron jobs (includes webhooks)](https://docs.openclaw.ai/automation/cron-jobs)
- [Internal hooks](https://docs.openclaw.ai/automation/hooks)
- [Multi-agent routing](https://docs.openclaw.ai/concepts/multi-agent)
- [Agent configuration](https://docs.openclaw.ai/gateway/config-agents)
- [Agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- [Sessions CLI](https://docs.openclaw.ai/cli/sessions)
- [Session tools and state changes](https://docs.openclaw.ai/concepts/session-tool)
- [Approvals CLI](https://docs.openclaw.ai/cli/approvals)
- [Exec approvals](https://docs.openclaw.ai/tools/exec-approvals)
- [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)
- [Browser CLI](https://docs.openclaw.ai/cli/browser)
- [Skills](https://docs.openclaw.ai/tools/skills)
- [Pull request review flow](https://docs.openclaw.ai/reference/pull-request-review-flow)
- [ClawSweeper](https://github.com/openclaw/clawsweeper)
- [Multi-agent sandbox tools](https://docs.openclaw.ai/tools/multi-agent-sandbox-tools)
- [API usage and costs](https://docs.openclaw.ai/reference/api-usage-costs)
- [GitHub Actions — `actions/setup-python`](https://github.com/actions/setup-python)
- [OpenClaw releases](https://github.com/openclaw/openclaw/releases)
