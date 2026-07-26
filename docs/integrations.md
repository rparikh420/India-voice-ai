# Integrations — wiring external services into OpenClaw via MCP

Companion to README Phase 5. This is the long version: exact commands, exact auth setup, exact
allowlists, and which of the three agents each server attaches to.

Everything here assumes the architecture from the README:

| Agent | Trust level | exec | Gets these servers |
|---|---|---|---|
| `main` | talks to you, holds your memory | **deny** | calendar, notion, files, memory, home |
| `triage` | reads untrusted content | **deny** | gmail, websearch |
| `dev` | does the work, sandboxed | allow (in Docker) | github |

Nothing gets the union. That's the whole point.

---

## The two rules

### Rule 1 — allowlist everything

Every MCP server ships more tools than you need. The Notion server ships ~20. The GitHub server
ships ~100 across 20+ toolsets. The filesystem server ships 13, four of which write. You will use a
handful of each.

Every tool you don't allowlist is attack surface you didn't have to carry. A prompt-injected email
can only reach the tools the model can see, so the allowlist *is* the blast radius. Filtering happens
at the OpenClaw registry layer, **before** the agent is ever told the tool exists — the model can't
be talked into calling something that isn't in its tool list.

Default posture: `--include` a named list. Never `--exclude` alone, because `--exclude` only defends
against tools that exist *today*. The server ships a new tool in a minor version and an
exclude-only filter silently grants it. An include list fails closed.

Use `--exclude` **on top of** `--include` only when you're allowlisting a glob and want to punch a
hole in it.

### Rule 2 — drafting is safe, sending is a one-way door

You can delete a bad note. You cannot unsend an email, unmerge a PR into a protected branch, or
un-delete a calendar event that someone else was relying on.

So the split is:

| Safe to automate | Requires a human |
|---|---|
| search, read, fetch, list | send, publish, merge, delete |
| create/update a **draft** | promote a draft to sent |
| add a label, add a comment | remove data, change access |
| create a branch, push to it | merge, force-push, run workflows |

Gmail gets `--exclude 'send_*'` and no send tool in the include list. Read the
[promotion criteria](#when-if-ever-to-promote-to-autonomous-sending) before you change that.

---

## How OpenClaw's MCP layer actually works

There are **three independent gates** between an MCP server and a model call. All three must pass.
People debug for an hour because they only configured one.

```
MCP server ships 100 tools
        │
        ▼  Gate 1 — mcp.servers.<name>.toolFilter   (openclaw mcp tools)
        │           include/exclude on raw MCP tool names, glob-aware
        ▼
     12 tools survive, registered as  github__get_file_contents, github__list_issues, …
        │
        ▼  Gate 2 — agents.entries.<id>.tools.allow / .deny   (per-agent policy)
        │           deny always wins; a non-empty allow blocks everything else
        ▼
   only the agents you named can see them
        │
        ▼  Gate 3 — tools.sandbox.tools.alsoAllow   (ONLY for sandboxed agents)
        │           a second allow gate; without it a sandboxed turn sees built-ins only
        ▼
     the model's tool list
```

### Gate 1 — the server-side tool filter

```bash
openclaw mcp tools <name> --include 'search_*,get_thread' --exclude 'admin_*'
openclaw mcp tools <name> --clear     # remove the filter entirely
```

`openclaw mcp configure <name>` also accepts filter flags alongside `--timeout`,
`--connect-timeout`, `--auth`, `--oauth-scope`, and `--probe`, so either command works. `mcp tools`
is the one the docs describe as the dedicated filter editor — prefer it, it's less to get wrong.

Entries are MCP tool names with simple `*` globs. In config it's:

```json5
mcp: { servers: { github: { toolFilter: { include: ["get_*", "list_*"], exclude: ["get_teams"] } } } }
```

### Gate 2 — per-agent tool policy

> **Correction to `openclaw.config.json5`.** That file currently uses
> `agents.entries.<id>.mcp.servers: ["github"]`. **There is no such config key.** It is not in
> `config-agents` or the configuration reference, and it is not in the schema. It will be ignored,
> which means every server is visible to every agent — the exact failure this whole architecture
> exists to prevent.
>
> The real mechanism is `agents.entries.<id>.tools.allow` with server-prefixed globs. Fix the config
> before you connect anything.

MCP tools appear in tool policy as `<server-prefix>__<tool>`, double underscore. The prefix is a
*provider-safe* transform of the server name, not the raw key: non-`[A-Za-z0-9_-]` characters become
`-`, names not starting with a letter get an `mcp-` prefix, and long or duplicate prefixes may be
truncated or suffixed. `mcp.servers["Outlook Graph"]` becomes `outlook-graph__*`.

**Name your servers in plain lowercase** — `gmail`, `calendar`, `notion`, `github`, `files` — and the
prefix equals the name. Do that and you never think about this again.

```json5
agents: {
  entries: {
    main:   { tools: { profile: "messaging", exec: { security: "deny" },
                       allow: ["calendar__*", "notion__*", "files__*", "memory__*"],
                       deny:  ["gmail__*", "github__*"] } },
    triage: { tools: { profile: "messaging", exec: { security: "deny" },
                       allow: ["gmail__*", "websearch__*"],
                       deny:  ["message", "browser", "github__*", "notion__*", "calendar__*"] } },
    dev:    { tools: { profile: "full", exec: { security: "allow" },
                       allow: ["github__*"],
                       deny:  ["gmail__*", "calendar__*", "notion__*"] } },
  },
}
```

Accepted entry forms: an exact name (`github__merge_pull_request`), a server glob (`github__*`), the
plugin id `bundle-mcp` (= *all* OpenClaw-managed MCP servers — useful for debugging, never as a
final policy), or a group like `group:plugins`.

The explicit `deny` lists are redundant given a non-empty `allow`, but keep them. They survive
someone later widening the allowlist, and they document intent.

### Gate 3 — the sandbox gate (this one bites)

`dev` runs with `sandbox.mode: "all"`. Sandboxed turns apply a **second** allow gate. If you skip
this, `dev` connects to GitHub fine, `openclaw mcp probe github` is green, and the agent still
insists it has no GitHub tools:

```json5
tools: {
  sandbox: {
    tools: {
      alsoAllow: ["github__*"],   // or "bundle-mcp" / "group:plugins" — prefer the narrow glob
    },
  },
}
```

### What `codex.agents` is (and isn't)

You'll find `mcp.servers.<name>.codex.agents: ["main"]` in the configuration reference. It is
**not** a general per-agent scoping mechanism. Per the docs it is "OpenClaw metadata for Codex
app-server threads only; it does not affect ACP sessions, generic Codex harness config, or other
runtime adapters."

If you drive Codex app-server threads, set it as defense in depth. Do not rely on it as your
isolation boundary — Gate 2 is the boundary.

```json5
codex: { agents: ["dev"], defaultToolsApprovalMode: "approve" }  // auto | prompt | approve
```

### Transport shapes

**stdio** (local process):

```json5
{ command: "npx", args: ["-y", "pkg", "/scoped/path"], cwd: "/srv/data", env: { TOKEN: "..." } }
```

**HTTP** (remote):

```json5
{
  url: "https://mcp.example.com/mcp",
  transport: "streamable-http",       // streamable-http | sse
  auth: "oauth",
  oauth: { scope: "docs.read" },
  headers: { Authorization: "Bearer ${TOKEN}" },
  requestTimeoutMs: 20000,
  connectionTimeoutMs: 5000,
  sslVerify: true,
  supportsParallelToolCalls: true,
}
```

### The env var safety filter

OpenClaw rejects interpreter-startup, loader-hijack, and shell-init env keys before spawning a stdio
server. Blocked prefixes include `NODE_OPTIONS`, `PYTHONSTARTUP`, `LD_*`, `DYLD_*`. Credential keys
are explicitly permitted: `GITHUB_TOKEN`, `AWS_ACCESS_KEY_ID`, `DATABASE_URL`, custom `*_API_KEY`.

This stops a malicious or compromised server definition from injecting code into the child
interpreter. It does **not** stop a malicious server package. Read Rule 1 again.

---

## The workflow, every time

Do this for each server. Do not skip step 3 — the allowlists below are written against the tool
lists I observed, and MCP servers add tools between releases.

```bash
# 1. add it, disabled, so nothing sees it yet
openclaw mcp add <name> [...transport flags...]
openclaw mcp configure <name> --disabled

# 2. authenticate (OAuth servers only)
openclaw mcp login <name>
openclaw mcp login <name> --code <code-from-browser>
openclaw mcp status --verbose

# 3. READ THE ACTUAL TOOL LIST before writing an allowlist
openclaw mcp probe <name> --json | jq -r '.tools[].name'

# 4. allowlist
openclaw mcp tools <name> --include '...'

# 5. attach to exactly one agent (edit tools.allow — see Gate 2)

# 6. enable and verify
openclaw mcp configure <name> --enabled
openclaw mcp doctor --probe
```

Note the CLI split: `openclaw mcp tools` **sets** the filter, `openclaw mcp probe` **lists** what the
server offers. They read like they'd be the other way round.

---

## Gmail → `triage`

**Agent: `triage`, and only `triage`.** Email is the single largest untrusted-input channel you own.
Every message is attacker-controlled text arriving in your agent's context. `triage` has `exec:
deny`, no outbound messaging, no memory persistence, and cannot see GitHub or the filesystem. It
reads mail and emits structured summaries; `main` acts on those summaries.

If Gmail were on `main`, a crafted email would be reading your calendar and writing to your notes.
If it were on `dev`, it would have a shell.

### Which server

Google now runs a **first-party remote MCP server** at `https://gmailmcp.googleapis.com/mcp/v1`.
Prefer it: no third-party code on your machine, no npm supply chain, OAuth handled by Google.

Caveat, stated plainly: as of mid-2026 it ships under the **Google Workspace Developer Preview
Program**, not GA. If your account isn't enrolled you cannot use it, and the tool surface is still
moving. Community fallback below.

### Google Cloud setup

1. Google Cloud console → create (or pick) a project.
2. **APIs & Services → Enable APIs**: enable **Gmail API** *and* **Gmail MCP API**.
3. **Google Auth Platform → Branding**: configure the OAuth consent screen. **External** + **Testing**
   is correct for a personal setup — add your own address as the sole test user and you never face
   verification review.
4. **Google Auth Platform → Clients → Create Client** → application type **Web application**. Note
   the client ID and secret.
5. **Data access**: add the least-privilege scope set below.

### Least-privilege scopes

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | search threads, read messages, list labels |
| `https://www.googleapis.com/auth/gmail.compose` | create and update **drafts** |
| `https://www.googleapis.com/auth/gmail.labels` | create/apply triage labels *(only if you use labelling)* |

Deliberately **not** requested:

- `gmail.modify` — read/write on everything including deletion. Far too broad.
- `https://mail.google.com/` — total mailbox control. Never.
- `gmail.send` — a dedicated send grant. You don't want one.

**Honest caveat about `gmail.compose`.** At the Gmail API level `gmail.compose` covers
create/read/update/delete of drafts *and* `drafts.send`. It is not a send-free scope. What actually
keeps you safe is that the MCP server exposes no send tool and your allowlist wouldn't include one
if it did. That's two layers, not three — treat the scope as "can send if the tooling ever lets it,"
and keep the allowlist tight. If you only ever want summaries and never drafts, drop `gmail.compose`
and run `gmail.readonly` alone; that *is* a hard guarantee.

### Install

```bash
openclaw mcp add gmail \
  --url https://gmailmcp.googleapis.com/mcp/v1 \
  --transport streamable-http \
  --auth oauth

openclaw mcp configure gmail --disabled
openclaw mcp login gmail
# browser → consent → copy the code
openclaw mcp login gmail --code <code>
openclaw mcp status --verbose
```

### Allowlist

Probe first — Google has been expanding this server and the count has moved:

```bash
openclaw mcp probe gmail --json | jq -r '.tools[].name'
```

Then:

```bash
openclaw mcp tools gmail \
  --include 'search_threads,get_thread,get_message,list_labels,list_drafts,create_draft,update_draft,label_message,label_thread' \
  --exclude 'send_*,delete_*,*_sensitive_*'
```

| Tool | In? | Why |
|---|---|---|
| `search_threads` | yes | the core triage primitive |
| `get_thread`, `get_message` | yes | read the thing you found |
| `list_labels` | yes | needs to know your label vocabulary |
| `list_drafts` | yes | avoids drafting a duplicate reply |
| `create_draft`, `update_draft` | yes | **the whole value.** Reversible |
| `label_message`, `label_thread` | yes | how triage output becomes visible in Gmail |
| `send_*` | **no** | one-way door. See below |
| `delete_label`, `unlabel_*` | **no** | destructive; no triage need |
| `create_label`, `update_label` | **no** | you define the taxonomy by hand, once |
| `apply_sensitive_*_label` | **no** | classification you should own, not delegate |

Note that the include list is explicit names, not `search_*` / `get_*` globs. That's deliberate — it
fails closed when Google ships new tools next month.

### When (if ever) to promote to autonomous sending

Short answer: probably never for general mail. If you do:

1. **Six-plus weeks of drafts you actually read**, with a near-zero rate of drafts you had to
   rewrite rather than tweak. If you're still editing tone, it isn't ready.
2. **Recipient allowlist, enforced outside the model** — a specific list of addresses, checked by a
   skill or hook, not by asking the model nicely in a prompt.
3. **Category allowlist** — meeting confirmations, "got it, will look tomorrow," calendar
   logistics. Never anything with a commitment, a number, or a person's feelings in it.
4. **An audit channel** — every send mirrored to `#inbox` in Discord, so you see it within minutes.
5. **A separate agent.** Do not add send to `triage`. Untrusted input and outbound send in the same
   context is the injection payload writing itself. Build a fourth agent that takes an
   already-approved structured draft and sends it, with no read access to raw mail.

If steps 2–5 sound like more work than approving drafts by hand — that's the correct conclusion.

### Community fallback (no Developer Preview access)

The Google Workspace MCP ecosystem is genuinely fragmented — there are a dozen-plus community
servers with overlapping names and no clear winner. The two most maintained:

```bash
# taylorwilsdon/google_workspace_mcp — Python, PyPI package "workspace-mcp"
openclaw mcp add gmail --command uvx \
  --arg workspace-mcp --arg --tools --arg gmail --arg --tool-tier --arg core \
  --env GOOGLE_OAUTH_CLIENT_ID=... --env GOOGLE_OAUTH_CLIENT_SECRET=...
```

`--tool-tier core` is itself a coarse allowlist — use it *and* Gate 1. Note that its core Gmail tier
includes `send_gmail_message`, so the `--exclude` is doing real work:

```bash
openclaw mcp tools gmail \
  --include 'search_gmail_messages,get_gmail_message_content,modify_gmail_message_labels,*_gmail_draft' \
  --exclude 'send_*'
```

Treat any community Gmail server as untrusted code holding a live OAuth token for your mailbox. Read
the source, pin the version, and don't run it as a general-purpose Workspace server when you only
want mail.

---

## Google Calendar → `main`

**Agent: `main`.** Calendar is your data, not inbound attacker content — invite text is
semi-untrusted, but you can't triage a calendar the way you triage mail. `main` needs it to answer
"what's my day look like" without a delegation round-trip, and `main` has no shell, so the blast
radius of a malicious invite description is bounded by the calendar allowlist itself.

`triage` must **not** get calendar. That combination — reads attacker mail, can write to your
schedule — is the one to avoid.

### Which server

Google's first-party remote server: `https://calendarmcp.googleapis.com/mcp/v1`. Same Developer
Preview caveat as Gmail.

Same Cloud project as Gmail. Enable **Google Calendar API** and **Calendar MCP API**.

### Least-privilege scopes

Read-only set (verified against Google's Calendar MCP configuration guide):

| Scope | Grants |
|---|---|
| `https://www.googleapis.com/auth/calendar.calendarlist.readonly` | `list_calendars` |
| `https://www.googleapis.com/auth/calendar.events.readonly` | `list_events`, `get_event`, `search_events` |
| `https://www.googleapis.com/auth/calendar.events.freebusy` | `suggest_time`, conflict detection |

**Start here.** Those three cover the morning brief, "what's on my calendar tomorrow," and "is
anything conflicting" — which is the README's Phase 5 exit criterion. You do not need write access
to get the main value of this integration.

If you later want the agent to actually book things, add `https://www.googleapis.com/auth/calendar.events`
(read/write on events, no calendar management). *Flagged: that write scope is the standard Calendar
API events scope, but I could not verify it against Google's MCP-specific docs — the pages 403 from
this environment. Confirm in the console before relying on it.*

Never request `https://www.googleapis.com/auth/calendar` (full calendar management, including
deleting calendars).

### Install

```bash
openclaw mcp add calendar \
  --url https://calendarmcp.googleapis.com/mcp/v1 \
  --transport streamable-http \
  --auth oauth

openclaw mcp configure calendar --disabled
openclaw mcp login calendar
openclaw mcp login calendar --code <code>
```

### Allowlist

Phase 1 — read-only, the default:

```bash
openclaw mcp tools calendar \
  --include 'list_calendars,list_events,get_event,search_events,suggest_time' \
  --exclude 'create_*,update_*,delete_*,respond_*'
```

Phase 2 — once you trust it, and only if you added the write scope:

```bash
openclaw mcp tools calendar \
  --include 'list_calendars,list_events,get_event,search_events,suggest_time,create_event,update_event' \
  --exclude 'delete_event,respond_to_event'
```

| Tool | In? | Why |
|---|---|---|
| `list_calendars` | yes | needs to know which calendars exist |
| `list_events`, `get_event`, `search_events` | yes | the brief, the conflict check |
| `suggest_time` | yes | free/busy math, no side effects |
| `create_event`, `update_event` | phase 2 | reversible, but visible to other attendees |
| `delete_event` | **no** | destructive, and silently notifies attendees |
| `respond_to_event` | **no** | RSVPs are social commitments. Yours to make |

`delete_event` is the one to be stubborn about. An agent that mis-parses "cancel my 3pm" and deletes
a recurring series has cost you data you cannot reconstruct, and sent cancellation notices to
everyone on it.

### Community fallback

`@cocal/google-calendar-mcp` (source: `nspady/google-calendar-mcp`) is the most active community
option — multi-account, cross-account conflict detection, actively released.

```bash
openclaw mcp add calendar --command npx --arg -y --arg @cocal/google-calendar-mcp \
  --env GOOGLE_OAUTH_CREDENTIALS=/Users/you/.config/gcal/credentials.json
```

Its tool names differ from Google's. Probe before allowlisting.

---

## Notion → `main`

**Agent: `main`.** Notes and knowledge capture are conversational — you say "write that down" in
Discord and it should land. `main` holds the context that makes filing decisions sensible.

Notion pages *can* carry injected content (anything shared into your workspace), which argues for
`triage`. The mitigation is that `main` has `exec: deny` and the allowlist below is narrow. If you
regularly ingest pages from outside your own workspace, move the read tools to `triage` and leave
only writes on `main`.

### Which server

**Notion's hosted remote server: `https://mcp.notion.com/mcp`.** OAuth-only, streamable-http, ~20
tools, and it's where Notion is actively investing.

The local `@notionhq/notion-mcp-server` (npm, v2.5.1) still works with an internal integration
token, but its own README says the repo may be sunset in favour of the hosted server. Use the remote
one unless you need self-hosting.

Auth is a one-click OAuth flow. **The integration inherits the permissions you personally have** —
there's no scope picker. Least privilege therefore has to come from two places: which pages you
share with the integration in Notion's UI, and the tool allowlist here. Do both.

Before connecting: in Notion, share only the specific databases and pages the assistant needs. Do
not share your workspace root.

### Install

```bash
openclaw mcp add notion \
  --url https://mcp.notion.com/mcp \
  --transport streamable-http \
  --auth oauth

openclaw mcp configure notion --disabled
openclaw mcp login notion
openclaw mcp login notion --code <code>
openclaw mcp status --verbose
```

### Allowlist

```bash
openclaw mcp tools notion \
  --include 'notion-search,notion-fetch,notion-create-pages,notion-update-page,notion-query-data-sources,notion-create-comment,notion-get-comments' \
  --exclude 'notion-create-database,notion-update-data-source,notion-create-view,notion-update-view,notion-move-pages,notion-duplicate-page,notion-get-users,notion-get-teams,notion-create-attachment,notion-download-attachment'
```

| Tool | In? | Why |
|---|---|---|
| `notion-search` | yes | find the page to append to |
| `notion-fetch` | yes | read it |
| `notion-create-pages` | yes | capture sweep, meeting notes — the point |
| `notion-update-page` | yes | append to running notes |
| `notion-query-data-sources` | yes | structured reads from your task DB |
| `notion-create-comment`, `notion-get-comments` | yes | low-stakes, useful for async back-and-forth |
| `notion-create-database`, `notion-update-data-source` | **no** | schema changes. An agent restructuring your database is a bad afternoon |
| `notion-create-view`, `notion-update-view` | **no** | same reason, plus you'd never ask for it in chat |
| `notion-move-pages`, `notion-duplicate-page` | **no** | silently reorganises your workspace; hard to notice, annoying to undo |
| `notion-get-users`, `notion-get-teams` | **no** | directory enumeration. No personal-assistant use case |
| `notion-*-attachment` | **no** | file movement in and out of the workspace is exfil surface |

There is no delete tool, which is convenient. Note the asymmetry: `notion-update-page` can still
overwrite content. Point captures at a dedicated inbox database rather than at pages you care about,
and the worst case is a messy inbox.

### Obsidian instead?

If your notes are local markdown, skip Notion and point the [filesystem server](#filesystem--main) at
your vault with `--include 'read_text_file,search_files,write_file'`. Fewer moving parts, no OAuth,
no third party, and the files are yours. The filesystem server has no concept of your vault's
structure, so you'll want a skill that encodes the conventions.

---

## GitHub → `dev`

**Agent: `dev`, exclusively.** GitHub is the one integration that pairs naturally with shell access:
`dev` clones, builds, tests, and pushes, and it does all of that inside Docker with
`sandbox.mode: "all"`.

`main` must not have GitHub. `triage` absolutely must not — issue bodies, PR descriptions, and
comments are attacker-controlled text, and an agent that reads them and has a shell is the exact
scenario the three-agent split exists to prevent. If you want PR digests in `main`, have `dev`
produce them and hand structured output back.

### Which server

> **Correction to README Phase 5.** It uses `@modelcontextprotocol/server-github`. **That package is
> deprecated** — npm marks it "Package no longer supported," last publish 2025-04-08, and the
> upstream source now lives in `modelcontextprotocol/servers-archived`. Do not install it. It was
> superseded by GitHub's own server.

Use **`github/github-mcp-server`**, GitHub's official implementation. Two ways to run it:

**Remote (recommended)** — `https://api.githubcopilot.com/mcp/`, OAuth 2.1 + PKCE, GA since Sept
2025. Nothing to install or update. It also supports URL-path scoping, which is a real allowlist
*before* OpenClaw's allowlist:

| URL | Scope |
|---|---|
| `https://api.githubcopilot.com/mcp/` | default toolsets |
| `https://api.githubcopilot.com/mcp/readonly` | default toolsets, read-only |
| `https://api.githubcopilot.com/mcp/x/repos` | repos only |
| `https://api.githubcopilot.com/mcp/x/pull_requests/readonly` | PRs, read-only |
| `https://api.githubcopilot.com/mcp/x/all` | everything (don't) |

Equivalent headers: `X-MCP-Toolsets`, `X-MCP-Readonly`, `X-MCP-Lockdown`.

**Local (Docker)** — `ghcr.io/github/github-mcp-server`, with `--toolsets` / `GITHUB_TOOLSETS`,
`--tools`, and `--read-only` / `GITHUB_READ_ONLY=1`. Use this if you need GitHub Enterprise Server,
or want the server's network traffic on your own machine.

### Install — remote, OAuth

```bash
openclaw mcp add github \
  --url https://api.githubcopilot.com/mcp/ \
  --transport streamable-http \
  --auth oauth \
  --header 'X-MCP-Toolsets: repos,issues,pull_requests,actions,context'

openclaw mcp configure github --disabled
openclaw mcp login github
openclaw mcp login github --code <code>
```

Start read-only for the first fortnight by pointing at `/mcp/readonly` instead. Flip the URL when
you're ready for writes.

### Install — local Docker, PAT

```bash
openclaw mcp add github \
  --command docker \
  --arg run --arg -i --arg --rm \
  --arg -e --arg GITHUB_PERSONAL_ACCESS_TOKEN \
  --arg -e --arg GITHUB_TOOLSETS \
  --arg ghcr.io/github/github-mcp-server \
  --env GITHUB_PERSONAL_ACCESS_TOKEN="$GITHUB_TOKEN" \
  --env GITHUB_TOOLSETS="repos,issues,pull_requests,actions,context"
```

`GITHUB_TOKEN` passes the env safety filter as an explicitly permitted credential var.

**Token scoping.** Use a **fine-grained PAT**, not a classic one. Select individual repositories —
never "all repositories." Permissions: Contents `read+write`, Pull requests `read+write`, Issues
`read+write`, Actions `read`, Metadata `read`. Nothing else. Set a 90-day expiry and rotate.

Never grant: `admin:org`, `delete_repo`, `workflow` (lets an agent modify CI definitions — which is
remote code execution on your runners), or org-level anything.

### Allowlist

`--toolsets` is a coarse filter; this is the fine one. Probe first, GitHub ships tools frequently.

```bash
openclaw mcp tools github \
  --include 'get_me,get_file_contents,search_code,list_commits,get_commit,list_branches,list_pull_requests,pull_request_read,search_pull_requests,list_issues,issue_read,search_issues,actions_list,actions_get,get_job_logs,get_check_run,create_branch,create_or_update_file,push_files,create_pull_request,update_pull_request,add_issue_comment,issue_write,pull_request_review_write,add_comment_to_pending_review' \
  --exclude 'merge_pull_request,*_pr_auto_merge,delete_file,create_repository,fork_repository,actions_run_trigger,run_secret_scanning,list_repository_collaborators,get_team*,search_users,request_copilot_review'
```

| Group | Tools | In? | Why |
|---|---|---|---|
| Identity | `get_me` | yes | server-side guidance says call it first for permission context |
| Read code | `get_file_contents`, `search_code`, `list_commits`, `get_commit`, `list_branches` | yes | no side effects |
| Read PRs/issues | `pull_request_read`, `list_pull_requests`, `search_pull_requests`, `issue_read`, `list_issues`, `search_issues` | yes | powers the PR-triage cron in Phase 7 |
| CI | `actions_list`, `actions_get`, `get_job_logs`, `get_check_run` | yes | CI-watch job needs logs to diagnose |
| Write code | `create_branch`, `create_or_update_file`, `push_files` | yes | **branches only.** A bad branch costs one `git push -d` |
| Write PRs | `create_pull_request`, `update_pull_request`, `pull_request_review_write`, `add_comment_to_pending_review`, `add_issue_comment`, `issue_write` | yes | a PR is a proposal. Proposals are safe |
| **Merge** | `merge_pull_request`, `enable_pr_auto_merge`, `disable_pr_auto_merge` | **no** | the one-way door. Merging is the human's job, always |
| **Destructive** | `delete_file`, `create_repository`, `fork_repository` | **no** | `delete_file` needs no automation; repo creation is namespace pollution you'll never clean up |
| **CI trigger** | `actions_run_trigger` | **no** | dispatching workflows is arbitrary compute on your runners with your secrets |
| **Security** | `run_secret_scanning` | **no** | surfaces secret values into an agent transcript. Absolutely not |
| **Directory** | `list_repository_collaborators`, `get_teams`, `get_team_members`, `search_users` | **no** | org enumeration; no dev-workflow value |

`merge_pull_request` is the GitHub analogue of `send_email` and deserves the same stubbornness.
Branch protection on `main` is a good second layer, but do not use it as your first — the allowlist
should mean the tool never appears.

### Don't forget Gate 3

`dev` is sandboxed. Without this it will report having no GitHub tools despite a green probe:

```json5
tools: { sandbox: { tools: { alsoAllow: ["github__*"] } } }
```

---

## Filesystem → `main`

**Agent: `main`.** Read-mostly access to a narrow, named directory. `dev` already reaches its code
through the sandbox workspace mount and does not need this server; giving it both is redundant
surface.

```bash
openclaw mcp add files \
  --command npx --arg -y \
  --arg @modelcontextprotocol/server-filesystem \
  --arg "$HOME/Documents/assistant"
```

`@modelcontextprotocol/server-filesystem` is one of the few first-party reference servers still
actively maintained (v2026.7.10). Directories are positional args; there is no config file, no
network access, and no auth.

**Point it at a dedicated directory, not `$HOME/Documents`.** Create `~/Documents/assistant` and put
only what the assistant should see in it. The server enforces path containment correctly, so the
directory list *is* the security boundary — which means the boundary is only as good as the path you
chose. Never pass `$HOME`, `/`, or a directory containing `.ssh`, `.aws`, `.env` files, or a
password-manager export.

### Allowlist

> **Correction to README Phase 5.** It uses `--include 'read_file,list_directory'`. **`read_file`
> no longer exists** — it was split into `read_text_file` and `read_media_file`. That include list
> silently yields one working tool.

```bash
openclaw mcp tools files \
  --include 'read_text_file,read_multiple_files,list_directory,search_files,get_file_info,directory_tree' \
  --exclude 'write_file,edit_file,move_file,create_directory,read_media_file,list_directory_with_sizes'
```

| Tool | In? | Why |
|---|---|---|
| `read_text_file`, `read_multiple_files` | yes | the point |
| `list_directory`, `directory_tree`, `search_files` | yes | finding things |
| `get_file_info` | yes | cheap metadata, useful for "what changed" |
| `read_media_file` | **no** | base64s binaries into context. Token bonfire, occasional parser surface |
| `write_file`, `edit_file` | **no** by default | see below |
| `move_file`, `create_directory` | **no** | reorganising your filesystem is not a thing you want to discover after the fact |
| `list_directory_with_sizes` | **no** | `list_directory` already covers it |

If you want capture-to-markdown (an Obsidian vault, a scratch notes dir), add `write_file` — but
then narrow the server's directory arg to *only* that vault, and run a **second** filesystem server
instance for read-only access elsewhere. Two servers with two allowlists beats one server with mixed
permissions:

```bash
openclaw mcp add vault --command npx --arg -y \
  --arg @modelcontextprotocol/server-filesystem --arg "$HOME/Obsidian/inbox"
openclaw mcp tools vault --include 'read_text_file,write_file,search_files,list_directory'
```

There is no delete tool in this server. Good.

---

## Web search → `triage`

**Agent: `triage`.** Search results are untrusted content by definition — a page you didn't choose,
written by someone you don't know, landing in your context. Same category as email, same agent.

This is also why `openclaw.config.json5` puts `browser` in `triage`'s deny list and turns off Discord
link previews: you want exactly one, controlled path for outside content to enter, and you want it to
land in the agent that can't do anything with it.

### Which server

No clear canonical winner; pick on retrieval style:

| Server | Package | Best at |
|---|---|---|
| Brave Search | `@brave/brave-search-mcp-server` (official, Brave Software) | independent index, no Google/Bing dependency |
| Tavily | `tavily-mcp` | agent-oriented, multi-step research mode |
| Exa | `exa-mcp-server`, or remote `https://mcp.exa.ai/mcp` | neural/semantic retrieval, returns full page content |

> Note: `@modelcontextprotocol/server-brave-search` is **archived**. Use `@brave/brave-search-mcp-server`.

Brave for a general assistant — independent index, straightforward API key, official maintainer:

```bash
openclaw mcp add websearch \
  --command npx --arg -y --arg @brave/brave-search-mcp-server \
  --env BRAVE_API_KEY="$BRAVE_API_KEY"

openclaw mcp tools websearch --include 'brave_web_search,brave_news_search'
```

*Flagged: I verified the package name and that it exposes web / local / image / video / news / summarizer capabilities, but I did not verify the exact tool identifiers. Run `openclaw mcp probe websearch --json` and correct the include list before enabling.*

Exclude image, video, and local/POI search: they're bulk context for a text assistant, and local
search leaks approximate location into a third-party API on every call.

The Exa remote server is the lowest-friction option if you'd rather not hold an API key locally:

```bash
openclaw mcp add websearch --url https://mcp.exa.ai/mcp --transport streamable-http --auth oauth
```

---

## Memory / knowledge graph → `main` (optional)

OpenClaw already has cross-conversation memory with embeddings (README Phase 3). An MCP memory
server is a *different* thing: an explicit, inspectable knowledge graph the agent writes to on
purpose, rather than an implicit recall layer.

Worth adding if you want "remember that Priya runs the platform team" to be a durable, greppable
fact rather than something the embedding search may or may not surface.

```bash
openclaw mcp add memory \
  --command npx --arg -y --arg @modelcontextprotocol/server-memory \
  --env MEMORY_FILE_PATH="$HOME/.openclaw/memory-graph.json"

openclaw mcp tools memory \
  --include 'create_entities,create_relations,add_observations,search_nodes,open_nodes,read_graph' \
  --exclude 'delete_entities,delete_relations,delete_observations'
```

**Agent: `main` only.** It holds personal facts about you and the people around you. `triage` must
never read it — that's a profile of your life sitting one injected email away from exfiltration.

Excluding the delete tools means the graph only grows. That's the right default: you can prune a
JSON file by hand in five minutes, and you cannot recover something an agent decided was stale.

*Flagged: `@modelcontextprotocol/server-memory` v2026.7.4 is verified on npm and actively released. The tool names above match the reference implementation's documented API but I did not probe a live instance — verify before enabling.*

---

## Home automation → `main` (optional)

Home Assistant ships a **built-in MCP Server integration** (core since 2025.2). No third-party
package: enable the integration, and it exposes your Assist API over MCP.

```bash
openclaw mcp add home \
  --url http://homeassistant.local:8123/mcp_server/sse \
  --transport sse \
  --header "Authorization: Bearer $HA_LONG_LIVED_TOKEN"
```

**Agent: `main`.** It's a conversational convenience ("turn the office lights off"), and it needs the
context of who's asking.

The real allowlist here is **not** in OpenClaw — it's Home Assistant's **exposed entities** page.
The MCP server surfaces whatever Assist can reach, so restrict there first: expose lights, climate,
and scenes; do not expose locks, garage doors, alarm arming/disarming, or cameras. An agent that can
unlock your front door based on text it read somewhere is not a convenience.

Note this is plain HTTP on your LAN. If Home Assistant is on a different host, put it behind
Tailscale rather than trusting the local network.

*Flagged: the `/mcp_server/sse` endpoint path is what Home Assistant's integration docs describe, but I did not verify it against a running instance, and it may have moved to streamable-http in a recent release. Check the integration page in your HA instance.*

---

## Credential storage

`~/.openclaw/` is the whole system: gateway token, MCP OAuth credentials, API keys, SQLite session
stores, and **your private transcripts**. Anyone with read access to that directory has your inbox,
your notes, and your GitHub.

MCP OAuth credentials specifically are stored in an owner-only SQLite database. `openclaw mcp
logout <name>` clears the credentials without removing the server definition — the right move before
lending your laptop or handing it in for repair.

```bash
# 1. FileVault. Non-negotiable on a laptop.
sudo fdesetup status          # expect: FileVault is On.
sudo fdesetup enable

# 2. Permissions — 600 on files, 700 on directories
openclaw security audit
openclaw security audit --fix

# 3. Verify
ls -ld ~/.openclaw            # drwx------
find ~/.openclaw -type f ! -perm 600 -print   # expect: nothing
```

Rules that matter:

- **No secrets in the config file.** Use `--env VAR=...` referencing your shell/launchd environment,
  or `${VAR}` interpolation in `headers`. `openclaw.config.json5` in this repo is committed; treat
  anything you put in it as public.
- **Prefer OAuth over long-lived tokens** where both exist (Gmail, Calendar, Notion, GitHub remote).
  OAuth credentials are revocable from the provider's side without touching your machine, and
  scope-limited by construction.
- **Rotate on a schedule.** The GitHub PAT gets a 90-day expiry. The gateway token gets rotated
  monthly per the README's audit cadence. Brave/Exa keys whenever you remember.
- **Back up encrypted, always.** `tar czf - ~/.openclaw | age -r <key> > backup.age`. An unencrypted
  backup of this directory in iCloud is a full compromise waiting for a bad sync.
- Sensitive values in `url` userinfo and in `headers` are redacted in OpenClaw's logs. Don't rely on
  that as your only protection — it covers logs, not process listings or config files.

### Revocation drill

Practise this once so you can do it under stress:

```bash
openclaw mcp logout gmail && openclaw mcp configure gmail --disabled
# then revoke at the source:
#   Google      → myaccount.google.com/permissions
#   Notion      → Settings → My connections
#   GitHub      → Settings → Applications, or delete the fine-grained PAT
openclaw gateway restart
```

---

## Troubleshooting

Work down this list. It's ordered by how often each thing is actually the problem.

### Diagnostics first

```bash
openclaw mcp status --verbose      # classifies transports WITHOUT connecting
openclaw mcp doctor --probe        # static checks + live connection to every enabled server
openclaw mcp probe github --json   # one server: tool count, resources, prompts, diagnostics
openclaw mcp show github           # the resolved definition
```

`status` never connects — a green `status` and a failing `doctor --probe` is normal and means the
definition is well-formed but the connection isn't working.

### The agent says it has no tools, but `probe` is green

The single most common failure, and it's never the MCP layer. Walk the three gates:

1. `openclaw mcp probe <name> --json | jq '.tools | length'` — Gate 1. If 0, your `toolFilter` is
   too tight or matches nothing (typo in a tool name, or a glob against names that changed).
2. Check `agents.entries.<id>.tools.allow` contains `<prefix>__*` — Gate 2. Remember the prefix is
   the provider-safe transform, not necessarily the raw key: `openclaw mcp show <name>` reveals it.
3. If the agent is sandboxed (`dev`), check `tools.sandbox.tools.alsoAllow` — Gate 3.

Also: **tool lists snapshot at session start.** Start a new session after any change. This alone
accounts for a lot of "it didn't work" that was actually "it worked, in the next session."

### stdio server won't start

```bash
npx -y @modelcontextprotocol/server-filesystem "$HOME/Documents/assistant"
```

Run the exact command by hand. Nine times in ten it's one of:

- `npx` not on the **daemon's** PATH. Your interactive shell has Homebrew's node; launchd may not.
  Use an absolute path: `--command /opt/homebrew/bin/npx`.
- A required env var dropped by the safety filter. `NODE_OPTIONS`, `PYTHONSTARTUP`, `LD_*`, `DYLD_*`
  are rejected by design and will not appear in the child process. If a server genuinely needs one,
  wrap it in a shell script that sets it internally — and think hard about why it needs one.
- Docker isn't running (the GitHub local install, and any sandboxed `dev` session).
- A relative `cwd`. Always absolute.

### HTTP server: connection or TLS failures

```bash
openclaw mcp configure <name> --connect-timeout 15000 --timeout 45000
```

- **Timeouts.** Defaults are tight. Notion and GitHub remote are occasionally slow on first
  connect; a cold `npx` download can exceed the connect timeout outright.
- **TLS through a corporate proxy.** `sslVerify: false` exists. Using it means any machine on the
  path can read your OAuth tokens. Install the proxy's CA properly instead.
- **404 on the MCP path.** Check the trailing slash — `https://api.githubcopilot.com/mcp/` has one.
- **Wrong transport.** `streamable-http` and `sse` are different protocols. If the server advertises
  streamable-http and you configured `sse`, you get a connect that hangs rather than a clean error.

### OAuth login fails or silently expires

```bash
openclaw mcp login <name>              # prints the authorization URL
openclaw mcp login <name> --code <code>
openclaw mcp status --verbose          # shows OAuth state
```

- The authorization code is **single-use and short-lived**. If you tabbed away for five minutes,
  restart the flow rather than reusing the code.
- Redirect URI mismatch (Google): the client's registered redirect must match what OpenClaw
  presents. Read the error text on Google's page — it names the URI it expected.
- Scope change: adding a scope after first consent does **not** re-prompt. Run
  `openclaw mcp logout <name>` then log in again to force a fresh consent screen.
- Google Developer Preview: if `login` returns 403/`PERMISSION_DENIED`, your account isn't enrolled
  in the Workspace Developer Preview Program. That's an access problem, not a config problem.
- Tokens live in an owner-only SQLite DB. If you restored `~/.openclaw/` from a backup onto a
  different machine or user, re-run `login`.

### Tool names don't match what's documented

Expected. MCP servers rename tools between releases and this document will drift.
`openclaw mcp probe <name> --json | jq -r '.tools[].name'` is the only authority. If an include list
stops matching, tools disappear silently — which is a safe failure, but a confusing one.

Build the habit: after any server update, re-probe and re-check the filter. Monthly, per the
README's audit cadence.

### It connects but the model won't use the tool

Not an MCP problem. Cheap models under-call tools — this is the Phase 2 tiering issue. `triage`
running Gemini Flash will sometimes summarise from thin air rather than calling `search_threads`.
Pin that task to the smart tier and see if the behaviour changes before you touch the MCP config.

---

## Summary table

| Server | Transport | Auth | Agent | Allowlist (`--include`) | Deliberately excluded |
|---|---|---|---|---|---|
| `gmail` | `streamable-http` → `gmailmcp.googleapis.com/mcp/v1` | OAuth (`gmail.readonly`, `gmail.compose`, `gmail.labels`) | **triage** | `search_threads,get_thread,get_message,list_labels,list_drafts,create_draft,update_draft,label_message,label_thread` | `send_*` (one-way door), `delete_*`, label CRUD, sensitive-label tools |
| `calendar` | `streamable-http` → `calendarmcp.googleapis.com/mcp/v1` | OAuth (`calendar.calendarlist.readonly`, `calendar.events.readonly`, `calendar.events.freebusy`) | **main** | `list_calendars,list_events,get_event,search_events,suggest_time` | `delete_event` (unrecoverable), `respond_to_event` (social commitment), writes until phase 2 |
| `notion` | `streamable-http` → `mcp.notion.com/mcp` | OAuth (inherits your workspace perms) | **main** | `notion-search,notion-fetch,notion-create-pages,notion-update-page,notion-query-data-sources,notion-create-comment,notion-get-comments` | schema/view changes, `move`/`duplicate`, user & team directory, attachments |
| `github` | `streamable-http` → `api.githubcopilot.com/mcp/` | OAuth 2.1 + PKCE (or fine-grained PAT) | **dev** | reads + `create_branch,create_or_update_file,push_files,create_pull_request,update_pull_request,issue_write,pull_request_review_write` | `merge_pull_request` (one-way door), auto-merge, `delete_file`, `actions_run_trigger`, `run_secret_scanning`, org directory |
| `files` | stdio `@modelcontextprotocol/server-filesystem` | none (path-scoped) | **main** | `read_text_file,read_multiple_files,list_directory,search_files,get_file_info,directory_tree` | all writes, `move_file`, `read_media_file` |
| `vault` *(optional)* | stdio, same package, narrowed dir | none | **main** | `read_text_file,write_file,search_files,list_directory` | everything else; separate instance from `files` |
| `websearch` | stdio `@brave/brave-search-mcp-server` | `BRAVE_API_KEY` | **triage** | web + news search only | image/video search, local/POI (location leak) |
| `memory` *(optional)* | stdio `@modelcontextprotocol/server-memory` | none | **main** | `create_entities,create_relations,add_observations,search_nodes,open_nodes,read_graph` | all `delete_*` — graph only grows |
| `home` *(optional)* | `sse` → Home Assistant | long-lived token | **main** | governed by HA exposed-entities, not OpenClaw | locks, garage, alarm, cameras — at the HA layer |

Cross-check: no agent appears in two rows that would put untrusted input and execution together.
`triage` holds gmail + websearch and has `exec: deny` with no outbound messaging. `dev` holds github
and a sandboxed shell but sees no external content. `main` holds your data and has no shell.

---

## Corrections to the rest of this repo

Found while researching. Not fixed here — this document only writes itself.

| Where | Problem |
|---|---|
| `openclaw.config.json5`, all three agents | `agents.entries.<id>.mcp.servers` is not a real config key. Servers are scoped via `agents.entries.<id>.tools.allow` with `server__*` globs. As written, every agent sees every server |
| `openclaw.config.json5`, `dev` | Sandboxed agents need `tools.sandbox.tools.alsoAllow: ["github__*"]` or MCP tools are invisible |
| README Phase 5, GitHub | `@modelcontextprotocol/server-github` is deprecated on npm and archived upstream. Use `github/github-mcp-server` |
| README Phase 5, filesystem | `--include 'read_file,list_directory'` — `read_file` was split into `read_text_file` / `read_media_file` and no longer exists |
| README Phase 5, Notion | `openclaw mcp add notion --url ... --auth oauth` is correct, but the OAuth flow needs the two-step `openclaw mcp login notion` → `--code <code>` |

## Package names I could not fully verify

Verified present on npm with a recent publish: `@modelcontextprotocol/server-filesystem` (2026.7.10),
`@modelcontextprotocol/server-memory` (2026.7.4), `@notionhq/notion-mcp-server` (2.5.1),
`@brave/brave-search-mcp-server` (2.1.0), `@cocal/google-calendar-mcp` (2.6.2), `exa-mcp-server`
(3.2.1), `tavily-mcp` (0.2.21). Confirmed deprecated: `@modelcontextprotocol/server-github`.

Not fully verified, flagged in place above:

- **Brave Search tool identifiers** (`brave_web_search`, `brave_news_search`) — the package is real
  and official, the tool names are conventional but unconfirmed. Probe first.
- **Memory server tool identifiers** — match the reference implementation's documented API; not
  probed live.
- **Home Assistant MCP endpoint path** (`/mcp_server/sse`) — from the integration docs; not verified
  against a running instance, and may have moved to streamable-http.
- **Google Calendar write scope** (`https://www.googleapis.com/auth/calendar.events`) — standard
  Calendar API scope, but Google's MCP-specific pages 403 from this environment so I could not
  confirm it's the scope the MCP server expects.
- **Exact Gmail MCP tool inventory** — Google's reference pages are not fetchable from here. The
  allowlist reflects a live tool list observed in a connected client, but Google is actively
  expanding this server in preview and secondary sources still describe a smaller 5-tool surface.
  Probe before enabling.
- **`workspace-mcp` (taylorwilsdon)** — documented as `uvx workspace-mcp` / `pip install
  workspace-mcp`; I read the README but did not verify the PyPI listing.

Anything below GA — the Google Gmail and Calendar remote MCP servers are both in the Workspace
Developer Preview Program — should be treated as subject to change without notice.

---

## Sources

**OpenClaw**

- [MCP client registry](https://docs.openclaw.ai/cli/mcp)
- [Configuration reference](https://docs.openclaw.ai/gateway/configuration-reference)
- [Tool policy](https://docs.openclaw.ai/gateway/config-tools)
- [Sandbox vs tool policy vs elevated](https://docs.openclaw.ai/gateway/sandbox-vs-tool-policy-vs-elevated)
- [Agent configuration](https://docs.openclaw.ai/gateway/config-agents)
- [Security](https://docs.openclaw.ai/gateway/security)
- [Policy CLI](https://docs.openclaw.ai/cli/policy)
- [OpenClaw on GitHub](https://github.com/openclaw/openclaw)

**Google Workspace**

- [Configure the Google Workspace MCP servers](https://developers.google.com/workspace/guides/configure-mcp-servers)
- [Configure the Gmail MCP server](https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server)
- [Gmail MCP reference — gmailmcp.googleapis.com](https://developers.google.com/workspace/gmail/api/reference/mcp)
- [Configure the Calendar MCP server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server)
- [Calendar MCP reference — calendarmcp.googleapis.com](https://developers.google.com/workspace/calendar/api/v3/reference/mcp)
- [Gmail API OAuth scopes](https://developers.google.com/gmail/api/auth/scopes)
- [Calendar API OAuth scopes](https://developers.google.com/calendar/api/auth)
- [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp)
- [nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp) · [@cocal/google-calendar-mcp on npm](https://www.npmjs.com/package/@cocal/google-calendar-mcp)

**Notion**

- [makenotion/notion-mcp-server](https://github.com/makenotion/notion-mcp-server)
- [Notion's hosted MCP server: an inside look](https://www.notion.com/blog/notions-hosted-mcp-server-an-inside-look)
- [@notionhq/notion-mcp-server on npm](https://www.npmjs.com/package/@notionhq/notion-mcp-server)

**GitHub**

- [github/github-mcp-server](https://github.com/github/github-mcp-server)
- [Remote server documentation](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- [Remote GitHub MCP Server is now generally available](https://github.blog/changelog/2025-09-04-remote-github-mcp-server-is-now-generally-available/)
- [Setting up the GitHub MCP Server](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/set-up-the-github-mcp-server)
- [modelcontextprotocol/servers-archived](https://github.com/modelcontextprotocol/servers-archived) — where `server-github` went

**Reference servers, search, home**

- [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)
- [@modelcontextprotocol/server-filesystem on npm](https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem)
- [@modelcontextprotocol/server-memory on npm](https://www.npmjs.com/package/@modelcontextprotocol/server-memory)
- [@brave/brave-search-mcp-server on npm](https://www.npmjs.com/package/@brave/brave-search-mcp-server)
- [Home Assistant — Model Context Protocol Server](https://www.home-assistant.io/integrations/mcp_server/)
