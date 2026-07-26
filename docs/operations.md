# Operations — the day-2 runbook

Phase 9 of the [README](../README.md) is the summary. This is the actual runbook: keeping it running,
keeping it cheap, fixing it when it breaks, and moving it off the laptop.

Assumes the setup in this repo: macOS host, launchd-managed Gateway, Discord, three agents
(`main` / `triage` / `dev`), OpenRouter models, heartbeat every 30m, and the seven cron jobs
installed by [`cron-jobs.sh`](../cron-jobs.sh).

---

## Corrections to the plan, found while writing this

The README and starter files were written from a higher-level read of the docs. Five things are
wrong or imprecise. Fix them before you rely on anything below.

| Where | Says | Actually |
|---|---|---|
| README Phase 9 | `npm install -g openclaw@latest` | Use `openclaw update`. It detects the install type, runs `openclaw doctor`, and coordinates the package swap with the running Gateway service. A manual `npm i -g` on a supervised install can load core files mid-swap. |
| `cron-jobs.sh` | `--deliver announce --target "$CH_BRIEF"` | The flags are `--announce --channel discord --to "channel:<id>"`. `--deliver` survives as a deprecated alias for `--announce`; `--target` is not a flag. Discord/Slack/Mattermost targets need the explicit `channel:<id>` or `user:<id>` prefix. |
| `openclaw.config.json5` | top-level `heartbeat: { ... }` | Heartbeat config lives at `agents.defaults.heartbeat` (merged with `agents.entries.<id>.heartbeat`). Note the precedence trap: **if any agent defines a `heartbeat` block, only those agents run heartbeats.** |
| `openclaw.config.json5` | `activeHours: { start, end, tz }` | The field is `timezone`, not `tz`. `end: "24:00"` is legal for end-of-day; `start` and `end` must differ or the window is zero-width (always outside). |
| `openclaw.config.json5` | heartbeat "gets the cheap tier" | The heartbeat block sets no `model`, so heartbeats run on the owning agent's primary — Kimi K2.6. Set `agents.defaults.heartbeat.model` explicitly if you want a different tier. See [Cost model](#3-cost-model) for why that may not be the saving you expect. |

Also worth knowing: `heartbeat.target` takes a channel id (`discord`) plus an optional `to` for the
recipient, and defaults to `none` — run-but-don't-deliver. `"discord:CHANNEL_ID"` is not the
documented shape for that field, though it is for cron delivery targets.

---

## 1. Operating cadence

The point of a cadence is to notice drift before it becomes an incident. Everything here is
read-only except where noted.

| When | Check | Command | Looking for |
|---|---|---|---|
| Daily (30s) | Gateway alive | `openclaw status` | `Runtime: running`, `Connectivity probe: ok` |
| Daily | Did the jobs fire | `openclaw cron list` | `lastRunStatus: ok`, sane `nextRunAtMs` |
| Daily | Did *you* read the output | — | Any channel you've started skimming is a job to delete |
| Weekly | Run history | `openclaw cron runs --id <jobId> --limit 20` | `skipped` / `error` streaks, especially on the two overnight jobs |
| Weekly | Spend | `openclaw gateway usage-cost --days 7 --all-agents` | Week-over-week drift; one job that doubled |
| Weekly | Security | `openclaw security audit` | New findings after config edits or plugin installs |
| Weekly | Heartbeat health | `openclaw system heartbeat last` | Skip reasons (`quiet-hours` is fine, `lanes-busy` repeatedly is not) |
| Weekly | Memory | ask the agent | The weekly-review job already prompts for this — actually read it |
| Monthly | MCP surface | `openclaw mcp doctor --probe` | Servers exposing more tools than their allowlist needs |
| Monthly | Skills | `openclaw skills update --all`, `openclaw skills verify <slug>` | Unused skills; trust-envelope failures |
| Monthly | Token rotation | `openclaw config set gateway.auth.token <new>` | — (write) |
| Monthly | Backup drill | see [Backup and restore](#5-backup-and-restore) | A backup you have never restored is not a backup |
| After every update | Re-audit | `openclaw doctor` → `openclaw gateway restart` → `openclaw security audit` | New features ship new surface |
| After any config edit | Validate | `openclaw config validate` | Catch it before the watcher silently skips it |

The single most useful daily habit is the third row. Cron output you've learned to ignore is worse
than no cron output — it costs money and trains you to skim the channel where the important
message will eventually appear.

### The command ladder

When something is wrong and you don't yet know what, run these in order. Stop when one of them
tells you something.

```bash
openclaw status
openclaw gateway status
openclaw logs --follow
openclaw doctor
openclaw channels status --probe
```

Healthy looks like: `Runtime: running`, `Connectivity probe: ok`, a `Capability:` line,
no blocking findings from doctor, and `works` / `audit ok` per channel account.

---

## 2. The laptop-sleep problem

Cron and heartbeat both run **inside the Gateway process**. There is no external scheduler
catching up for you. When the Mac sleeps, the Gateway sleeps: nothing fires, and Discord messages
sit at the platform until the process comes back. On startup, overdue *isolated* agent-turn jobs
are rescheduled rather than replayed immediately — which is good for boot-storm avoidance and bad
if you were hoping the 07:00 brief would appear at 09:15.

Everything below is mitigation. The only actual fix is a host that doesn't sleep — see
[Migrating to a VPS](#7-migrating-to-a-vps).

### Mitigation, in order of effectiveness

**1. Don't sleep on AC.** System Settings → Battery → Options → *Prevent automatic sleeping on
power adapter*, plus *Wake for network access*. This covers a desk-bound Mac almost completely.

**2. Scheduled wake windows** so the morning jobs have a live process to run in:

```bash
# Wake (or power on) every day at 06:45 — 15 minutes before the 07:00 brief
sudo pmset repeat wakeorpoweron MTWRFSU 06:45:00

# Verify what's actually scheduled
pmset -g sched
```

`wakeorpoweron` powers the machine on if it's off, not just out of sleep. Give yourself a buffer:
waking at 06:59 for an 07:00 job leaves no time for channel reconnect and model bootstrap.
Remember the top-of-hour stagger — a `0 7 * * 1-5` job is auto-delayed by up to 5 minutes unless
you pass `--exact`.

**3. `caffeinate` for bounded work sessions.** Not a daemon strategy — a "don't sleep while I'm
away from the desk for two hours" strategy:

```bash
caffeinate -dimsu -t 7200        # block display/idle/system sleep for 2h
```

Wrapping the launchd Gateway in `caffeinate -s` pins the machine awake for as long as the Gateway
runs, which is to say permanently, which is to say you've disabled sleep with extra steps. Prefer
the Battery setting; it's honest about what it's doing and survives reinstalls.

**4. If the Mac is meant to be a server** (a Mac mini on a shelf), stop pretending it's a laptop:

```bash
sudo pmset -a sleep 0 disksleep 0 standby 0 powernap 0
```

This significantly reduces — but does not eliminate — Maintenance Sleep. The system still performs
some maintenance sleeps for TCP keepalive and mDNS upkeep.

### Diagnosing what actually slept or woke the machine

`pmset -g assertions` answers "why is it awake right now":

```bash
pmset -g assertions
```

Look at the `PreventUserIdleSystemSleep` / `PreventSystemSleep` counts and the process list
underneath. If a value is `1` and you didn't expect it, the listed process is holding the machine
up. If they're all `0` and the machine is awake, nothing is preventing sleep and it will drop as
soon as the idle timer expires.

`pmset -g log` answers "what happened last night":

```bash
pmset -g log | grep -iE "sleep|wake|maintenance" | tail -50
```

Two signatures matter for OpenClaw specifically:

- `Entering Sleep state due to 'Maintenance Sleep'` — Power Nap. Brief, but it puts the Wi-Fi
  driver into state 0. Any outbound `connect()` landing in that window can fail with `ENETDOWN`
  even though the host has full connectivity the rest of the time.
- `en0 driver is slow (msg: WillChangeState to 0)` aligned with a crash timestamp — same cause,
  clearer evidence.

Cross-reference against OpenClaw's own crash evidence:

```bash
ls ~/.openclaw/logs/stability/ | tail -5
openclaw gateway stability --bundle latest
```

A `*-uncaught_exception.json` bundle with `error.code` of `ENETDOWN`, `ENETUNREACH`,
`EHOSTUNREACH`, or `ECONNREFUSED` and a stack pointing into Node `net` `lookupAndConnect` is the
Power Nap flap. Releases from `2026.5.26` onward classify these as benign transient network errors
instead of letting them reach the top-level uncaught handler — if you're seeing them terminate the
process, upgrade first.

### The launchd respawn gate

This one is genuinely nasty and worth understanding. After a crash burst, macOS launchd applies an
undocumented respawn-protection gate that can stop honoring `KeepAlive=true` until something
external re-arms it — interactive login, a dashboard connection, an SSH session. The symptom is
the Gateway being dead for an hour and then coming back the moment you touch the machine, which
makes it look like the Gateway was fine and *you* were imagining things.

```bash
launchctl print gui/$UID/ai.openclaw.gateway | grep -E "state|last exit|runs"
tail -n 80 ~/Library/Logs/openclaw/gateway.log
```

`state = not running`, an incrementing `runs` count across the day, and **no**
`received SIG*; shutting down` line in the log means transient crashes, not clean restarts. Clean
shutdowns log a signal; crashes don't.

Watchdog, on a 5-minute schedule (system `cron`, or a LaunchAgent — deliberately *outside* the
Gateway, because a scheduler inside the thing you're supervising is not a supervisor):

```bash
#!/bin/sh
state=$(launchctl print gui/$UID/ai.openclaw.gateway 2>/dev/null | awk -F'= ' '/state =/ {print $2; exit}')
if [ "$state" != "running" ]; then
  launchctl kickstart -k gui/$UID/ai.openclaw.gateway
fi
```

The point is the external kick. `KeepAlive=true` alone is not sufficient on macOS after a crash
burst.

### Detecting missed runs

Cron persists run history in SQLite, so a missed window is visible after the fact:

```bash
openclaw cron list                              # nextRunAtMs, lastRunStatus per job
openclaw cron runs --id <jobId> --limit 30      # the actual history
openclaw cron runs --id <jobId> --run-id <runId>
```

A gap in the timestamps with no `error` row is a sleep gap: the Gateway wasn't there to record a
failure. An `error` row means it ran and failed — a different problem. Recurring jobs back off on
consecutive errors (30s → 1m → 5m → 15m → 60m) and reset after the next success, so an error
streak also *delays* the next attempt.

Turn on failure alerts so you don't have to poll:

```bash
openclaw cron edit <jobId> --failure-alert --failure-alert-after 2 \
  --failure-alert-channel discord --failure-alert-to "channel:YOUR_DEV_CHANNEL_ID"
```

Add `--failure-alert-include-skipped` on jobs where a skip matters as much as an error. Skipped
runs are counted separately and do not feed the error backoff.

### Design around it, not against it

The starter jobs are already written to say "if this is running late because my machine was
asleep, say so." Keep that property. Concretely:

- **Idempotent, not incremental.** "Summarize what I missed" survives a gap; "report what changed
  since your last run" quietly loses a day.
- **Widen the windows.** Inbox triage at `0 8-20/2 * * *` has seven chances to catch up. A single
  `0 8 * * *` has one.
- **Don't chain jobs.** If the capture sweep assumes the evening review ran, a sleep gap corrupts
  both.
- **Accept the honest limit.** If you need the morning brief to be there at 07:00 every day
  without exception, you need a host that is on at 07:00 every day without exception.

---

## 3. Cost model

Two things make this workload predictable: heartbeats and cron jobs run on fixed schedules, and
isolated sessions keep each run's context roughly constant instead of growing with conversation
history.

**Prices change. Verify before trusting the totals.** Figures below were checked on
**2026-07-26** against OpenRouter listings. Re-check at
[openrouter.ai/moonshotai/kimi-k2.6](https://openrouter.ai/moonshotai/kimi-k2.6) and
[openrouter.ai/google/gemini-3.5-flash](https://openrouter.ai/google/gemini-3.5-flash), or pull
the live catalog:

```bash
curl -s https://openrouter.ai/api/v1/models \
  | jq -r '.data[] | select(.id|test("kimi-k2|gemini-3.5-flash")) | [.id,.pricing.prompt,.pricing.completion] | @tsv'
```

(`pricing.prompt` / `pricing.completion` are dollars **per token**; multiply by 1e6 for the
per-million figures below.)

| Model | Role in this setup | $/M input | $/M output |
|---|---|---|---|
| `moonshotai/kimi-k2.6` | primary — `main`, `dev` | 0.60 | 3.41 |
| `moonshotai/kimi-k2.5` | fallback | ~0.375 | ~2.03 |
| `google/gemini-3.5-flash` | configured "cheap" tier — `triage` | 1.50 | 9.00 |
| `google/gemini-3.5-flash-lite` | *what the cheap tier should be* | 0.30 | 2.50 |

### Read that table again

**Gemini 3.5 Flash is 2.5× the input price and 2.6× the output price of Kimi K2.6.** The starter
config routes the highest-frequency job in the whole system — inbox triage, ~213 runs/month — to
the most expensive model it configures, on the assumption that "Flash" means "cheap." It doesn't,
not on OpenRouter, not at this version.

Fix it by pointing the cheap alias at Flash **Lite**:

```json5
models: { aliases: { cheap: "openrouter/google/gemini-3.5-flash-lite" } }
```

This is the general lesson: the cheap tier is whatever the catalog says is cheap this month, not
whatever has "flash" or "mini" in the name. Re-run the price check when you re-check the changelog.

### The arithmetic

Formula, per line:

```
monthly_cost = runs_per_month × ( input_tokens/1e6 × price_in  +  output_tokens/1e6 × price_out )
```

Run counts derive from the configured schedules. Month = 30.44 days, 21.7 weekdays, 4.35 weeks.

| Workload | Schedule | Runs/mo | Model | In/run | Out/run | Cost/mo |
|---|---|---:|---|---:|---:|---:|
| Heartbeat | 30m, 08:00–22:00 | 852 | Kimi K2.6 | 4K | 300 | **$2.91** |
| Morning brief | `0 7 * * 1-5` | 22 | Kimi K2.6 | 35K | 1.5K | **$0.57** |
| Inbox triage | `0 8-20/2 * * *` | 213 | Gemini 3.5 Flash | 30K | 1.2K | **$11.89** |
| Evening review | `0 21 * * *` | 30 | Kimi K2.6 | 20K | 1K | **$0.47** |
| Weekly review | `0 17 * * 0` | 4 | Kimi K2.6 | 40K | 2K | **$0.13** |
| Capture sweep | `0 22 * * *` | 30 | Kimi K2.6 | 20K | 1K | **$0.47** |
| PR triage | `*/30 9-18 * * 1-5` | 434 | Kimi K2.6 | 18K | 600 | **$5.58** |
| Security audit | `0 9 * * 1` | 4 | Kimi K2.6 | 25K | 1.5K | **$0.08** |
| Interactive chat | ~25 turns/day | 760 | Kimi K2.6 | 25K | 700 | **$13.21** |
| | | | | | | **≈ $35/mo** |

Worked example, so you can redo any row: inbox triage input is
`213 × 30,000 = 6.39M` tokens → `6.39 × $1.50 = $9.59`; output is `213 × 1,200 = 0.256M` →
`0.256 × $9.00 = $2.30`; total `$11.89`.

**Where the token estimates come from and how wrong they can be.** Heartbeat is the only
well-anchored number — the docs state ~2–5K/run for isolated + `lightContext`, vs ~100K without.
The cron figures are estimates for a multi-step isolated agent turn where each tool call re-sends
the accumulated turn context; a job that makes 5 MCP calls costs several times a job that makes 1.
Interactive is the least predictable line and the one most likely to be badly wrong — it scales
with how much you actually talk to the thing. Treat **$25–$60/month** as the honest band and
`openclaw gateway usage-cost` as the source of truth:

```bash
openclaw gateway usage-cost --days 30 --all-agents
openclaw gateway usage-cost --days 7 --agent triage --json
```

Also spending money outside this table: **memory embeddings** (`memory.search` defaults to an
OpenAI embedding provider — set `provider: "ollama"` or `"local"` to zero it out), **media
understanding** on inbound images/audio, and **web search** providers. All small at this volume,
all non-zero, none visible in the model-token math above.

### Levers, ranked by impact

| # | Lever | Saving | Cost to you |
|---|---|---|---|
| 1 | `isolatedSession: true` on heartbeat | ~$48/mo | None. Already on. Never turn it off. |
| 2 | Route triage to Flash **Lite**, not Flash | ~$9.30/mo | None — it's cheaper *and* the current alias is a mistake |
| 3 | PR triage `*/30` → `0 */2` on weekdays | ~$4.20/mo | Slower PR notifications |
| 4 | `activeHours` 14h instead of 24h | ~$2.10/mo | Already on. Also the thing stopping 3am pings. |
| 5 | `lightContext: true` on heartbeat | ~$0.60/mo | Heartbeat loses workspace bootstrap files |
| 6 | `HEARTBEAT_OK` silence | **$0** | — |

Lever 1 is the whole game. At ~100K tokens/run × 852 runs, a non-isolated heartbeat is
**85.2M input tokens/month = $51** on Kimi — more than the entire rest of the system combined.
Every other lever is rounding error next to it.

Lever 6 deserves the explicit zero. `HEARTBEAT_OK` suppresses the *message*; the agent turn still
ran and you still paid for it. It buys you a quiet Discord channel, which is worth a lot, but it
is not a cost control. Same for `target: "none"` — the run happens either way.

Two more that don't fit the table:

- **Cut jobs you don't read.** A job whose output you skim is 100% waste, not 20%. The weekly
  review already asks which job to kill; act on the answer.
- **`--model` per job.** `openclaw cron edit <jobId> --model <ref>` pins one job's model without
  touching the agent. Use it to move a specific expensive job down a tier, or a specific
  failure-prone one up. Configured fallback chains still apply, because cron `--model` is a job
  primary rather than a session override.

---

## 4. Updating

Releases move fast — v2026.7.1 alone carried 3,063 contributions from 532 contributors. Minor
versions change behaviour. The changelog is not optional reading.

### Before

```bash
openclaw --version
openclaw update status --json          # channel + availability
openclaw update --dry-run              # what it would actually do
```

Read the release notes at
[github.com/openclaw/openclaw/releases](https://github.com/openclaw/openclaw/releases). Look
specifically for: changed config defaults, stricter validation, renamed keys, and anything in the
security or channel sections. Most post-upgrade breakage is config drift or a stricter default
finally being enforced — not a bug.

Then take a real recovery point. `openclaw update` preserves an automatic pre-update **config**
copy; it does not create a full state recovery point.

```bash
mkdir -p ~/Backups/openclaw
openclaw backup create --output ~/Backups/openclaw --verify
```

### Update

```bash
openclaw update
```

It detects the install type (npm / pnpm / Bun / git), fetches the release, runs `openclaw doctor`,
and restarts the Gateway. It stages the candidate into a temporary npm prefix, validates the Node
version during `preinstall`, verifies the packaged `dist` inventory, and only then swaps the clean
tree into the real global prefix — so a failed install restores the old package automatically.

Prefer it over `npm i -g openclaw@latest`. On a supervised install, a package manager replacing
files underneath a running Gateway can have it load core or plugin files mid-swap. If you must go
manual, stop the service first:

```bash
openclaw gateway stop
npm i -g openclaw@latest
openclaw gateway install --force
openclaw gateway restart
```

On macOS with the Hub app in local Gateway mode, the dashboard card reads **Update Mac app +
Gateway**: Sparkle updates the app, and after relaunch the app runs `openclaw update --tag
<app-version>` against its own Gateway. Let it drive; don't race it from the CLI.

Auto-update is off by default. Leave it off — you want to read the changelog first.

### After

```bash
openclaw doctor            # migrates config, audits DM policies, checks health
openclaw gateway restart
openclaw health
openclaw security audit    # new features ship new surface
openclaw cron list         # confirm the scheduler re-armed
```

If channels are empty or model calls 401 afterward:

```bash
openclaw status --all
openclaw gateway status --deep
openclaw doctor --fix
openclaw gateway restart
```

`plugin load failed: dependency tree corrupted` under Channels means the channel config survived
but plugin registration didn't — `doctor --fix` handles it. Provider 401s after re-auth are
usually stale per-agent OAuth shadows, which `doctor --fix` also clears.

### Rollback

Two layers, and you want the first one:

1. **Code-only** — reinstall older OpenClaw, keep current state.
2. **State restore** — only when the older code genuinely cannot read the migrated config or
   database. This discards everything since the backup.

```bash
npm view openclaw versions --json
openclaw update --tag <known-good-version> --dry-run
openclaw update --tag <known-good-version>
```

`openclaw update --tag` is preferred over a raw package-manager install: it detects the downgrade,
asks for confirmation, runs plugin convergence and compatibility checks, refreshes service
metadata, restarts, and verifies the running version. If your stored channel is
`extended-stable`, you must pass `--channel stable --tag <version>` — exact tags can't combine
with that selector.

During incident recovery, set `OPENCLAW_NO_AUTO_UPDATE=1` in the Gateway environment so an enabled
auto-updater doesn't immediately re-apply the release you just backed out of.

**Downgrading across the session SQLite migration** needs one extra step before starting the older
binary:

```bash
openclaw gateway stop
openclaw doctor --session-sqlite restore --session-sqlite-all-agents
```

This restores archived legacy transcript artifacts. It does not delete SQLite data — but sessions
created after the migration exist only in SQLite and will be invisible to the older runtime.

Verify any rollback the same way you verify an update:

```bash
openclaw --version
openclaw health
openclaw plugins list --json
openclaw gateway status --deep --json
openclaw doctor --lint --json
```

---

## 5. Backup and restore

### What's in there

`~/.openclaw/` is the entire system.

| Path | What | Recreatable? |
|---|---|---|
| `openclaw.json` | active config (JSON5) | By hand, painfully |
| `credentials/` | channel + provider credentials | Only by re-pairing everything |
| `agents/<id>/agent/openclaw-agent.sqlite` | per-agent sessions, transcripts, auth profiles | **No** |
| shared state SQLite | cron jobs, run history, background tasks, delivery queue | No |
| `workspace/` | `SOUL.md`, `USER.md`, `AGENTS.md`, `MEMORY.md` | Only by rewriting them |
| `skills/` | managed + local skills | Reinstallable |
| `extensions/` | installed plugin source + manifests | Reinstallable |
| `logs/stability/` | crash bundles | Disposable |
| `dev/`, `git/`, `npm/`, `tools/` | managed checkouts and downloaded runtimes | Yes — and excluded from backups |

### The warning

**This archive contains your private transcripts, your live channel tokens, and your provider
credentials.** Anyone holding it can read every conversation you've had with the assistant and
send messages as your bot. Encrypt it. Store it with owner-only permissions. Don't put it in a
cloud-synced folder unencrypted, and don't hand it to a support thread.

Turn on FileVault on the host, and keep the live directory at 600/700 —
`openclaw security audit --fix` does that.

### Taking a backup

Use the first-class command rather than `tar`. It excludes live-mutation paths before `tar` reads
them (avoiding size/write races), captures SQLite through the online backup API, `VACUUM`s the
private copies so deleted pages don't leak into the archive, and never copies live `-wal` / `-shm`
files.

```bash
openclaw backup create --output ~/Backups/openclaw --verify
openclaw backup create --output ~/Backups/openclaw --no-include-workspace   # smaller/faster
openclaw backup create --only-config                                        # smallest; works on a broken config
openclaw backup verify ~/Backups/openclaw/<archive>.tar.gz
```

Then encrypt:

```bash
BK=$(ls -t ~/Backups/openclaw/*.tar.gz | head -1)
age -r age1yourpublickey... < "$BK" > "$BK.age" && rm "$BK"
# or: gpg --encrypt --recipient you@example.com "$BK" && rm "$BK"
chmod 600 "$BK.age"
```

Weekly is a reasonable cadence; before every update is mandatory. Schedule it in **system** `cron`
or a LaunchAgent — not OpenClaw cron. You want backups to keep working when the Gateway is the
thing that's broken.

```bash
# ~/bin/openclaw-backup.sh, run from system cron: 0 3 * * 0
set -euo pipefail
DEST="$HOME/Backups/openclaw"
openclaw backup create --output "$DEST" --verify
BK=$(ls -t "$DEST"/*.tar.gz | head -1)
age -r age1yourpublickey... < "$BK" > "$BK.age"
chmod 600 "$BK.age"
rm -f "$BK"
ls -t "$DEST"/*.tar.gz.age | tail -n +9 | xargs -r rm -f   # keep 8
```

For a single database rather than the whole state tree:

```bash
openclaw backup sqlite create --agent main --repository ~/Backups/openclaw-sqlite
openclaw backup sqlite list --repository ~/Backups/openclaw-sqlite
```

Never copy live `.sqlite`, `-wal`, `-shm`, or `-journal` files as a portability artifact. Copy
completed snapshot directories only.

### Restoring — and the drill

Be clear about the limitation: **`openclaw backup` archives support creation and verification, not
in-place whole-archive activation.** Restore is an explicit, offline, operator-driven step. There
is no `openclaw backup restore`.

The drill. Do it once now, then quarterly. It takes fifteen minutes and it is the only thing that
turns a backup into a recovery plan.

```bash
# 1. Verify the archive is intact and decryptable
age -d -i ~/.age/key.txt ~/Backups/openclaw/2026-07-26-openclaw-backup.tar.gz.age \
  > /tmp/restore-test.tar.gz
openclaw backup verify /tmp/restore-test.tar.gz

# 2. Extract to staging — never over the live tree
mkdir -p /tmp/openclaw-restore && tar xzf /tmp/restore-test.tar.gz -C /tmp/openclaw-restore

# 3. Read the manifest. It maps archive layout back to source paths and records
#    the OpenClaw version the archive came from.
jq '.' /tmp/openclaw-restore/manifest.json

# 4. Confirm the things that actually matter are present and non-empty
ls -l /tmp/openclaw-restore/**/agents/*/agent/openclaw-agent.sqlite
ls -l /tmp/openclaw-restore/**/credentials/
ls -l /tmp/openclaw-restore/**/workspace/*.md

# 5. Clean up — this staging directory holds your credentials in cleartext
rm -rf /tmp/openclaw-restore /tmp/restore-test.tar.gz
```

For a real restore onto a new host: install the same OpenClaw version the manifest names, stop the
Gateway, copy the staged tree into place using the manifest's source mapping, run
`openclaw doctor`, then start. Expect to reinstall plugins — nested `node_modules/` trees are
excluded as rebuildable artifacts, so use `openclaw plugins update <id>` or
`openclaw plugins install <spec> --force` if a restored plugin reports missing dependencies.

Two things a portable archive can't give you:

- **Volatile artifacts** (`.jsonl` / `.log` transcript and run-history files, delivery queue
  entries) are deliberately excluded. If you need byte-for-byte recovery including those, stop the
  Gateway and take a filesystem, volume, or VM snapshot instead. The JSON result's
  `skippedVolatileCount` tells you how many files were omitted.
- **A working config, if yours is broken.** `openclaw backup create` fails fast on an invalid
  config when workspace discovery is enabled. Use `--no-include-workspace` or `--only-config` to
  get a partial backup out during an incident.

---

## 6. Troubleshooting

Start with the [command ladder](#the-command-ladder). Then find your symptom.

| Symptom | Diagnose | Fix |
|---|---|---|
| **Gateway won't start** | `openclaw gateway status`, `openclaw logs --follow`, `openclaw doctor` | `Gateway start blocked: set gateway.mode=local` → set `gateway.mode: "local"` or re-run `openclaw setup`. `refusing to bind gateway ... without auth` → you bound non-loopback without a token/password. `EADDRINUSE` / `another gateway instance is already listening` → port conflict; `openclaw gateway status --deep` finds duplicate launchd units. |
| **Daemon crash-loops** | `launchctl print gui/$UID/ai.openclaw.gateway \| grep -E 'state\|last exit\|runs'`; sample PIDs 4× over 30s; `tail -n 80 ~/Library/Logs/openclaw/gateway.log` | Rotating PIDs + `EADDRINUSE` on macOS usually means **both** `ai.openclaw.gateway` and `ai.openclaw.node` LaunchAgents are loaded and each injects `OPENCLAW_LAUNCHD_LABEL`. `openclaw node uninstall` (only if you don't use node features), or install the documented wrapper via `openclaw gateway install --wrapper <path> --force`. Verify with `openclaw gateway status --deep --require-rpc`. |
| **Gateway goes quiet, revives when you touch it** | `pmset -g log \| grep -iE 'sleep\|wake'`; `openclaw gateway stability --bundle latest` | Power Nap `ENETDOWN` flap plus the launchd respawn gate. See [§2](#the-launchd-respawn-gate). Upgrade past `2026.5.26`, add the `launchctl kickstart -k` watchdog. |
| **Gateway dies under load** | `openclaw gateway stability --bundle latest`, `openclaw gateway diagnostics export` | `Reason: diagnostic.memory.pressure.critical`, `V8 heap:` near limit, `Largest session files:` naming a huge `.jsonl`. Prune or archive that session; reduce concurrent work. If `Gateway heap: not set`, regenerate service metadata with `openclaw gateway install --force`. Ambient shell `NODE_OPTIONS` is intentionally ignored. |
| **Channel disconnects / no replies** | `openclaw channels status --probe`, `openclaw pairing list --channel discord`, `openclaw config get channels`, `openclaw logs --follow` | `drop guild message (mention required` → `requireMention` gating. `pairing request` → sender needs approval. `blocked` / `allowlist` → policy filtered it. `missing_scope` / `Forbidden` / `401` / `403` → bot token or Discord permissions. |
| **Cron job silently not firing** | `openclaw cron status`, `openclaw cron list`, `openclaw cron runs --id <jobId> --limit 20` | `cron: scheduler disabled` → `cron.enabled` or `OPENCLAW_SKIP_CRON`. Gap with no error row → host was asleep ([§2](#2-the-laptop-sleep-problem)). Wrong hour → cron without `--tz` uses the **host** timezone; `--at` without a timezone is **UTC**. Fires more often than expected → day-of-month and day-of-week use OR logic (`0 9 15 * 1` = every 15th *and* every Monday); use croner's `+` modifier (`0 9 15 * +1`). Job vanished → one-shots auto-delete after success unless `--keep-after-run`. Whole job disabled → archiving a bound session disables its cron jobs, and restoring the session does **not** re-enable them: `openclaw cron enable <jobId>`. |
| **Cron fired, nothing delivered** | `openclaw cron show <jobId>` (shows the *resolved* route) | Delivery mode `none`; missing or invalid `channel`/`to`; Discord target missing the `channel:` prefix; run returned only `NO_REPLY` (suppressed by design); channel auth error. |
| **Heartbeat not running** | `openclaw system heartbeat last`, `openclaw logs --follow` | Skip reasons: `quiet-hours` (outside `activeHours` — expected), `empty-heartbeat-file` (monitor scratch is only scaffolding), `dm-blocked` (`directPolicy: "block"`), `lanes-busy` / `cron-in-progress` (deferred behind other work — chronic means jobs are overlapping). Also: if *any* agent defines a `heartbeat` block, only those agents heartbeat. |
| **MCP server unreachable** | `openclaw mcp status --verbose` (no connection), `openclaw mcp doctor` (static checks), `openclaw mcp doctor --probe` (live) | Static: missing stdio command, bad cwd, missing TLS files, server disabled, literal secrets in headers/env, incomplete OAuth. Live failure on an OAuth server → `openclaw mcp login <name>`. Tools missing but server up → `toolFilter.include/exclude`, or `tools.profile: "minimal"` hiding MCP tools entirely. Repeated protocol failures briefly pause that server so one broken MCP doesn't consume the whole turn. |
| **Model provider errors / rate limits** | `openclaw logs --follow`, `openclaw models status --json`, `openclaw status --usage` | Fallback chain is `kimi-k2.6 → kimi-k2.5 → openrouter/auto`; failures advance it and set a cooldown, and a user-visible fallback notice appears. Persistent 401s after re-auth → `openclaw doctor --fix` clears stale per-agent OAuth shadows. For transport-level debugging use targeted flags, not global `debug`: `OPENCLAW_DEBUG_MODEL_TRANSPORT=1`, `OPENCLAW_DEBUG_SSE=events`. |
| **Sandbox / Docker failures** (`dev` agent) | `docker info`, `openclaw doctor`, `openclaw sandbox list` | Docker daemon not running is the usual answer. `openclaw-sandbox:bookworm-slim` missing → OpenClaw fails fast with a build instruction rather than substituting a plain image; build it. Bind mount rejected → system paths, Docker socket dirs, and credential roots (`~/.ssh`, `~/.aws`, `~/.config`) are blocked by default. After changing mounts: `openclaw sandbox recreate`. Docker settings configured while `sandbox.mode` is off is a `security audit` warning, not a working sandbox. |
| **Runaway token spend** | `openclaw gateway usage-cost --days 7 --all-agents`, then `--agent <id>` | Check whether `isolatedSession` got turned off, whether a heartbeat is retrying inside a busy loop, whether a cron job started failing and is retrying, and whether a session grew huge (`Largest session files:` in the stability bundle). Pin the offending job down a tier with `openclaw cron edit <jobId> --model <cheap-ref>`. |
| **Config change not taking effect** | `openclaw config file`, `openclaw config validate`, `openclaw logs --follow` | `config reload skipped (invalid config)` → the watcher rejected your edit and kept the old runtime config; it does **not** rewrite `openclaw.json`. Look for `openclaw.json.rejected.*` / `.clobbered.*` beside the config. `openclaw doctor --fix` repairs or restores last-known-good. Also: `gateway.*` (port, bind, auth, TLS) needs a **restart**; everything else hot-applies. And the config path must be a **regular file** — OpenClaw writes atomically via rename, so a symlinked `openclaw.json` gets its target replaced instead of written through. |
| **Everything broke right after an update** | `openclaw status --all`, `openclaw update status --json`, `openclaw gateway status --deep` | Usually config drift or a stricter default now enforced, not a bug. `openclaw doctor --fix`, then restart. If service config and runtime still disagree: `openclaw gateway install --force && openclaw gateway restart`. Then [rollback](#rollback). |

### Where things live

| | |
|---|---|
| Gateway file log (JSONL, daily, 24h retention) | `/tmp/openclaw/openclaw-YYYY-MM-DD.log` |
| macOS app-owned Gateway log | `~/Library/Logs/openclaw/gateway.log` |
| Crash / stability bundles | `~/.openclaw/logs/stability/` |
| Active config | `openclaw config file` (usually `~/.openclaw/openclaw.json`) |
| Rejected / repaired config copies | `<config>.rejected.*`, `<config>.clobbered.*` (newest 32 kept) |
| Sessions | `~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite` |
| launchd unit | `~/Library/LaunchAgents/ai.openclaw.gateway.plist` |

```bash
openclaw logs --follow                          # live tail via RPC
openclaw logs --follow --json                   # structured
openclaw channels logs --channel discord        # channel-scoped
openclaw --log-level debug gateway run          # one-off verbose run
```

`--verbose` only affects console and WebSocket log verbosity — it does not change file log levels.
Use `logging.level` or `OPENCLAW_LOG_LEVEL` for that. Prefer the targeted
`OPENCLAW_DEBUG_MODEL_*` flags over turning everything to `debug`.

### Filing a bug

```bash
openclaw gateway diagnostics export --output ~/openclaw-diagnostics.zip
```

Attach that rather than pasting raw logs. The bundle is payload-free: operational metadata,
redacted relative paths, memory readings, queue state — no message text, webhook bodies, tokens,
cookies, or raw session ids.

---

## 7. Migrating to a VPS

### When it's worth it

Migrate when at least two of these are true. Not before — a VPS is a second machine to maintain,
and you should learn the system on the laptop first.

- You've missed the morning brief more than twice in a month because the Mac was asleep.
- You're checking `openclaw cron runs` to find out whether a job fired. That's a supervision
  problem, and supervision problems don't get better.
- You want the assistant to answer Discord while you're travelling, closed-lid.
- You've added a webhook-driven job (CI autofix, GitHub events) — inbound events don't queue
  usefully against a sleeping host.
- Your `pmset`/`caffeinate` workarounds have accumulated to the point where they're now a system
  you maintain.

Reasons **not** to migrate: cost (a €5/mo VPS is a rounding error against $35/mo of tokens); the
`dev` agent needing your local repos (a VPS makes that harder, not easier); or wanting the Mac Hub
app's Canvas, voice, and screen features — those live on the Mac regardless, and can stay there as
a paired **node** against a remote Gateway.

### Pre-flight checklist

- [ ] Current OpenClaw version noted: `openclaw --version`
- [ ] Verified backup taken **and test-restored**: `openclaw backup create --verify` + the [drill](#restoring--and-the-drill)
- [ ] Discord bot token, `OPENROUTER_API_KEY`, `GITHUB_TOKEN` retrievable from your password manager — not only from the laptop's shell profile
- [ ] Cron job list exported: `openclaw cron list --all --json > cron-jobs.json`
- [ ] Workspace files (`SOUL.md`, `USER.md`, `AGENTS.md`, `MEMORY.md`) in version control or copied
- [ ] MCP servers inventoried: `openclaw mcp status --verbose`
- [ ] Host chosen. Documented and working: Hetzner, DigitalOcean, Oracle Always-Free (ARM), Fly.io, Azure, GCP, Hostinger, Northflank, Railway, exe.dev, Raspberry Pi. AWS EC2/Lightsail also works. 2 vCPU / 4 GB and an **SSD-backed** disk is comfortable for this workload.
- [ ] Tailscale account ready
- [ ] A maintenance window. Both hosts running the same channel tokens simultaneously will fight over Discord.

### Step by step

**1. Harden the box before OpenClaw touches it.** Install Tailscale, join the VPS to your tailnet,
**verify a second SSH session over the Tailscale IP or MagicDNS name**, then restrict public SSH.
Do this first — locking yourself out is easier than it sounds.

**2. Install OpenClaw and the daemon.**

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
openclaw onboard --install-daemon      # installs a systemd *user* unit
```

**3. Stop the laptop Gateway** before moving state, so you're not copying a live database and not
running two Gateways against one Discord bot.

```bash
# on the Mac
openclaw gateway stop
openclaw backup create --output ~/Backups/openclaw --verify
```

**4. Move state.** Ship the archive over the tailnet, extract to staging, and place files using
the manifest's source mapping. Expect to reinstall plugins (`node_modules/` trees are excluded)
and to re-create managed runtimes under `dev/`, `git/`, `npm/`, `tools/` (also excluded).

```bash
scp ~/Backups/openclaw/<archive>.tar.gz.age vps:/tmp/
# on the VPS
age -d -i ~/.age/key.txt /tmp/<archive>.tar.gz.age > /tmp/oc.tar.gz
openclaw backup verify /tmp/oc.tar.gz
mkdir -p /tmp/oc-restore && tar xzf /tmp/oc.tar.gz -C /tmp/oc-restore
jq '.' /tmp/oc-restore/manifest.json
# place ~/.openclaw per the manifest, then:
openclaw doctor
shred -u /tmp/oc.tar.gz /tmp/<archive>.tar.gz.age && rm -rf /tmp/oc-restore
```

Set secrets in the **service** environment, not an interactive profile — a systemd user unit does
not read your login shell's exports.

```bash
systemctl --user edit openclaw-gateway.service
```

**5. Network posture — keep it loopback.**

```json5
gateway: {
  bind: "loopback",
  auth: { mode: "token" },
  tailscale: { mode: "serve" },   // HTTPS + identity headers, tailnet-only
}
```

Serve keeps the Gateway on `127.0.0.1` while Tailscale terminates HTTPS and provides routing. SSH
tunnelling is the universal fallback and works from anywhere:

```bash
ssh -N -L 18789:127.0.0.1:18789 user@vps
# then openclaw health / openclaw status --deep hit ws://127.0.0.1:18789
```

Do **not** use `tailscale.mode: "funnel"` for a personal assistant — that's public HTTPS, and
OpenClaw refuses to start it without password auth for exactly that reason. If you bind `lan` or
`tailnet`, a shared secret (`gateway.auth.token` or `gateway.auth.password`) becomes mandatory
unless auth is delegated to a correctly configured trusted proxy.

**6. Tune systemd.**

```ini
[Service]
Environment=OPENCLAW_NO_RESPAWN=1
Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
Restart=always
RestartSec=2
TimeoutStartSec=90
```

```bash
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
loginctl enable-linger "$USER"     # keeps the user unit alive without a login session
```

On small or ARM hosts (Oracle Always-Free, Raspberry Pi), also add to the shell profile so
interactive CLI calls get the same benefit:

```bash
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
mkdir -p /var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1
```

`NODE_COMPILE_CACHE` cuts repeated command startup time (first run warms it). `OPENCLAW_NO_RESPAWN=1`
keeps routine restarts in-process, which keeps PID tracking simple under a supervisor. If you
installed a *system* unit instead of a user unit, edit it with `sudo systemctl edit
openclaw-gateway.service` — and note that hardened units need
`ReadWritePaths=/var/lib/openclaw /home/<user>/.openclaw /tmp` so plugin installs and doctor
cleanup can persist.

**7. Re-pair channels.** Credentials came across in the archive, but pairing and device identity
are per-install and per-host:

```bash
openclaw channels status --probe
openclaw pairing list --channel discord
openclaw devices list
openclaw mcp login notion          # re-run OAuth for every OAuth-backed MCP server
```

Discord's bot token itself is portable; the DM pairing approvals may not be. Send yourself a DM
and approve the pairing again if prompted.

**8. Fix host-specific config.** Things that were correct on the Mac and are wrong now:

- `agents.entries.dev.workspace: "~/code"` — that path doesn't exist on the VPS
- `sandbox.mode: "all"` needs Docker installed on the new host
- Any absolute path in MCP server definitions (`@modelcontextprotocol/server-filesystem` scoped to
  `$HOME/Documents`)
- `activeHours.timezone` if the VPS is in a different region — cron without `--tz` uses the
  **host** timezone, so every job without an explicit `--tz` just moved
- `logging.file`, if you'd overridden it

**9. Turn the Mac into a node** (optional, recommended). Point the Hub app at the remote Gateway
instead of running a local one; you keep Canvas, voice, screen capture, and `system.run` on the
Mac while the Gateway stays always-on. For a persistent tunnel, the docs describe an SSH
`LocalForward` entry plus a LaunchAgent that keeps it alive across reboots.

**10. Decommission the laptop Gateway.** Don't leave it installed-but-stopped; a reboot will
helpfully start it again and you'll have two Gateways on one Discord bot.

```bash
# on the Mac, once the VPS is verified
openclaw gateway stop
launchctl bootout gui/$UID/ai.openclaw.gateway 2>/dev/null || true
```

### Verification checklist

- [ ] `openclaw gateway status` → `Runtime: running`, `Connectivity probe: ok`, `Capability:` line
- [ ] `openclaw doctor` → no blocking findings
- [ ] `openclaw health` → green
- [ ] `openclaw channels status --probe` → Discord `works` / `audit ok`
- [ ] DM the bot from your phone → real answer
- [ ] `openclaw cron list` → all 7 jobs present, sane `nextRunAtMs`, correct timezone
- [ ] `openclaw cron run <morning-brief-id> --wait` → completes `ok` and lands in `#brief`
- [ ] `openclaw system heartbeat last` → recent, no unexpected skip reason
- [ ] `openclaw mcp doctor --probe` → every server reachable
- [ ] `openclaw security audit` → clean; auth is on and bind is still loopback
- [ ] `openclaw gateway usage-cost --days 1` → non-zero and not absurd
- [ ] Reboot the VPS. Everything comes back without you touching it. **This is the whole reason you migrated** — verify it.
- [ ] 24h later: `openclaw cron runs --id <jobId>` for every job shows the overnight runs actually fired

Then re-run the backup drill against the new host. The old backups reference the old paths.

---

## Sources

- [Linux server / VPS](https://docs.openclaw.ai/vps)
- [Updating](https://docs.openclaw.ai/install/updating)
- [Logging](https://docs.openclaw.ai/logging)
- [Gateway troubleshooting](https://docs.openclaw.ai/gateway/troubleshooting)
- [Gateway diagnostics export](https://docs.openclaw.ai/gateway/diagnostics)
- [Gateway health](https://docs.openclaw.ai/gateway/health)
- [Restart recovery](https://docs.openclaw.ai/gateway/restart-recovery)
- [Heartbeat](https://docs.openclaw.ai/gateway/heartbeat)
- [Cron jobs](https://docs.openclaw.ai/automation/cron-jobs)
- [Cron CLI](https://docs.openclaw.ai/cli/cron)
- [Gateway CLI](https://docs.openclaw.ai/cli/gateway)
- [Backup CLI](https://docs.openclaw.ai/cli/backup)
- [Doctor](https://docs.openclaw.ai/cli/doctor)
- [Security CLI](https://docs.openclaw.ai/cli/security)
- [MCP CLI](https://docs.openclaw.ai/cli/mcp)
- [Configuration + hot reload](https://docs.openclaw.ai/gateway/configuration)
- [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing)
- [Tailscale](https://docs.openclaw.ai/gateway/tailscale)
- [Remote access](https://docs.openclaw.ai/gateway/remote)
- [API usage and costs](https://docs.openclaw.ai/reference/api-usage-costs)
- [Token use and costs](https://docs.openclaw.ai/reference/token-use)
- [Model failover](https://docs.openclaw.ai/concepts/model-failover)
- [macOS app](https://docs.openclaw.ai/platforms/macos)
- [Diagnostics flags](https://docs.openclaw.ai/diagnostics/flags)
- [OpenClaw releases](https://github.com/openclaw/openclaw/releases)
- [Kimi K2.6 on OpenRouter](https://openrouter.ai/moonshotai/kimi-k2.6)
- [Kimi K2.5 on OpenRouter](https://openrouter.ai/moonshotai/kimi-k2.5)
- [Gemini 3.5 Flash on OpenRouter](https://openrouter.ai/google/gemini-3.5-flash)
- [Gemini 3.5 Flash Lite on OpenRouter](https://openrouter.ai/google/gemini-3.5-flash-lite)
