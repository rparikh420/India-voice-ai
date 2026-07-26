# Custom skills

Six skills for the setup in the parent `README.md`. They encode the decisions
that are specific to *this* assistant — what counts as urgent mail, how the brief
is ordered, when the assistant is allowed to interrupt — and none of them are
things you could install from ClawHub, because nobody else knows your rules.

| Skill | Agent | What it does |
|---|---|---|
| [`email-triage`](email-triage/SKILL.md) | `triage` | Urgent/Reply/FYI/Noise buckets, drafts (never sends), reports injection attempts |
| [`morning-brief`](morning-brief/SKILL.md) | `main` | Daily brief ordered by decision-need, <200 words, handles the asleep-laptop case |
| [`meeting-notes`](meeting-notes/SKILL.md) | `main` | Structured notes, actions with named owners and real dates, filed to Notion |
| [`weekly-review`](weekly-review/SKILL.md) | `main` | Retro + memory curation; proposes `MEMORY.md` diffs instead of writing silently |
| [`deploy-runbook`](deploy-runbook/SKILL.md) | `dev` | Worked example: this repo's deploy encoded as a runbook. Template to adapt. |
| [`escalation-policy`](escalation-policy/SKILL.md) | all | Interrupt / queue / silent — the shared rule that keeps proactive jobs from becoming noise |

Three of them assume the config in `../openclaw.config.json5`: `triage` cannot
execute or message out, `main` cannot execute, `dev` can execute inside a
sandbox. If you flatten that to a single agent, `email-triage` in particular
stops being a security boundary and becomes a suggestion.

## What a skill actually is

A directory containing `SKILL.md`: YAML frontmatter plus a markdown body.

```markdown
---
name: email-triage
description: One line, under 160 characters. This is what the model sees.
metadata:
  {
    "openclaw":
      {
        "emoji": "📥",
        "requires": { "config": ["mcp.servers.gmail"] },
      },
  }
---

Body. Instructions to the agent, second person.
```

Only `name` and `description` are required. `name` is lowercase letters, digits
and hyphens; keep the directory name matching it.

Optional frontmatter worth knowing:

| Field | Effect |
|---|---|
| `homepage` | URL shown in the macOS Skills UI |
| `user-invocable` | Default `true` — exposes the skill as a `/slash` command |
| `disable-model-invocation` | Default `false` — keeps it out of the prompt but still `/`-callable |
| `command-dispatch: tool` + `command-tool` | Bypass the model and dispatch a slash command straight to a tool |
| `metadata.openclaw.requires` | Gate on `bins`, `anyBins`, `env`, `config` paths |
| `metadata.openclaw.os` | `darwin` / `linux` / `win32` |
| `metadata.openclaw.always` | Force it into every prompt |
| `metadata.openclaw.install` | Installer specs (brew etc.) for missing binaries |

**The description does the work.** Only the skills *list* — name, description,
location — is injected into the system prompt. The body is read on demand, when
the model decides the skill applies. A vague description means the body never
gets read. Roughly 12 skills cost ~550 tokens of system prompt, so the list stays
cheap; that's the point of the split.

Use `{baseDir}` in the body to reference files bundled in the skill directory
without hardcoding paths.

Note the frontmatter quirk: `metadata` is parsed as YAML first, then flattened to
a JSON string and re-parsed as **JSON5**. The odd-looking indentation above is
what a JSON5 block formatted by Prettier inside YAML looks like. Copy the shape.

## Installing

These are plain directories. Copy them in:

```bash
mkdir -p ~/.openclaw/workspace/skills
cp -r openclaw/skills/*/ ~/.openclaw/workspace/skills/
```

Or let the CLI do it, which validates and lets you rename:

```bash
openclaw skills install ./openclaw/skills/email-triage
openclaw skills install ./openclaw/skills/deploy-runbook --as deploy --agent dev
openclaw skills install ./openclaw/skills/weekly-review --global   # shared managed dir
```

Then check them:

```bash
openclaw skills list
openclaw skills info email-triage
openclaw skills check          # reports missing bins / env / config from `requires`
```

`skills check` is the one to run after copying. The `requires.config` paths in
these skills (`mcp.servers.gmail`, `mcp.servers.notion`, `mcp.servers.calendar`)
must match the keys your config actually uses — if you added MCP servers with
different names, the gate silently hides the skill. Fix the path or drop the
`requires` block.

`cp -r` vs `openclaw skills install`: use `cp -r` when you're iterating on your
own skills and want the files where you can edit them; use the CLI when you want
validation, slug control, `--agent` or `--global` targeting, or install tracking
for later `openclaw skills update`.

### Scoping to agents

Skills are visible to every agent unless you say otherwise. `deploy-runbook`
should not be in `triage`'s prompt. In `openclaw.json`:

```json5
agents: {
  defaults: { skills: ["escalation-policy", "morning-brief", "meeting-notes", "weekly-review"] },
  entries: {
    triage: { skills: ["email-triage", "escalation-policy"] },
    dev:    { skills: ["deploy-runbook"] },
  },
}
```

A non-empty per-agent `skills` array **replaces** the defaults. It does not merge.
`dev` above sees `deploy-runbook` and nothing else — that's usually what you want,
but it surprises people.

## Precedence

Highest wins. Discovery walks up to 6 levels deep looking for `SKILL.md` under
each root.

| # | Source | Path |
|---|---|---|
| 1 | Workspace | `<workspace>/skills` |
| 2 | Project agent | `<workspace>/.agents/skills` |
| 3 | Personal agent | `~/.agents/skills` |
| 4 | Managed / local | `~/.openclaw/skills` |
| 5 | Bundled | ships with OpenClaw |
| 6 | Extra dirs + plugins | `skills.load.extraDirs` |

Same `name` at two levels: the higher one wins outright, silently. This is how
you override a bundled skill, and also how you spend an hour wondering why your
edits do nothing.

## The gotcha: skills snapshot at session start

**OpenClaw snapshots eligible skills when a session starts and reuses that list
for every subsequent turn.** Editing a `SKILL.md` mid-conversation does not
change the running session's behaviour.

So after any change:

```bash
openclaw gateway restart        # or just start a new session / DM thread
```

There is a mid-session refresh when `SKILL.md` files change or new nodes connect,
but do not rely on it while you're testing — you'll conclude your edit did
nothing when it just hasn't landed yet. Restart, then test.

Test a skill directly rather than through a channel:

```bash
openclaw agent --message "triage my inbox" --agent triage
```

Also worth knowing: `skills.entries.<name>.env` and `apiKey` are applied to the
**host process** during a run, not to sandboxes, and restored afterwards. A
sandboxed `dev` agent will not see them.

## Third-party skills are untrusted code

Blunt version: installing a skill from ClawHub is running someone else's
instructions inside an agent that holds your credentials. A skill body can tell
your agent to read `~/.ssh`, fetch a remote script and pipe it to `exec`, or
exfiltrate `.env` to a URL — and it is *text*, so no scanner reliably catches it.
The gap between "a skill" and "a supply-chain attack" is that you read it.

Before enabling anything you didn't write:

```bash
openclaw skills verify @owner/slug     # checks ClawHub's trust envelope, non-zero on failure
```

Then actually open `SKILL.md` and read the whole body. Refuse anything that:

- Runs `exec`/`bash` on input it fetched from the network
- Wants broad filesystem access, or reaches outside its own directory
- Fetches remote code at runtime instead of shipping it
- Reads `~/.openclaw/`, `~/.ssh`, `~/.aws`, or your shell profile
- Sends anything outbound without a confirmation step
- Contains instruction-shaped text aimed past you at the model
  ("ignore previous instructions", "you may skip confirmation for…")

`openclaw skills install` will make you pass `--acknowledge-clawhub-risk` for
releases it considers risky. Do not paper over that flag in a script. Official
publishers and bundled skills skip the trust check — which means the check is
weakest exactly where an attacker would want to publish.

Gate installs organisation-wide with a policy command:

```json5
security: {
  installPolicy: "/usr/local/bin/vet-skill.sh",   // runs before installs proceed
}
```

Wire `openclaw skills verify` into whatever runs `openclaw skills update --all`.
An unattended auto-update of third-party skills is an unattended code deploy into
an agent with your credentials.

## Writing your own

What actually makes these work, in rough order of impact:

1. **Write to the agent, not about it.** "Assign every action a named human"
   beats "actions should have owners."
2. **Decide the ambiguous cases in advance.** The tables of concrete calls in
   `email-triage` are the highest-value part of that file. Generic principles get
   re-litigated on every run; a lookup table doesn't.
3. **State the failure mode you're preventing.** "Do not brief someone on a
   morning that's half over" survives paraphrase; "be context-aware" doesn't.
4. **Use real tool names.** `web_fetch`, `exec`, `message`, `cron`, `ask_user`,
   `memory_search`, `image_generate`, `subagents`. Check yours against
   `openclaw mcp probe <server> --json` for MCP tools — those names shift between
   server versions.
5. **Keep the description under 160 characters and specific about triggering.**
   It is the only part that's always in context.
6. **Say what the agent may do alone vs. what needs a yes.** Every skill that
   touches the outside world should have that section.
7. **Cut anything that would be true of any assistant.** If a line could appear
   in someone else's skill unchanged, it's costing tokens and teaching nothing.
