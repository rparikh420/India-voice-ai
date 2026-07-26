# Channels — the complete reference for this setup

Companion to the [main plan](../README.md). Phase 4 gets Discord working; this document is
everything after that: why Discord, how to do Slack properly, how to run both without leaking work
context into personal memory, and what the mobile/voice story actually is.

Three corrections to the README and `openclaw.config.json5` land in here. They're flagged inline
with **Correction:** and collected in [Config keys the README gets wrong](#config-keys-the-readme-gets-wrong).

---

## 1. Choosing a channel

OpenClaw ships or plugs into 29 channels. Only three are in the core install — **iMessage,
Telegram, and WebChat**. Everything else, including Discord and Slack, is a plugin:

```bash
openclaw plugins install @openclaw/slack       # documented verbatim in the Slack docs
openclaw plugins install @openclaw/discord     # same pattern: @openclaw/<id>
openclaw channels add --channel discord        # or let onboarding install it on demand
openclaw gateway restart                       # plugins need a restart to register
```

> **Correction:** the README's Phase 4 implies Discord works straight out of the box. It doesn't —
> Discord is an *official plugin*. `openclaw onboard` and `openclaw channels add` will install it
> for you, but if you're hand-editing config you need the `plugins install` step and a Gateway
> restart, or the channel silently never registers.

### Comparison

| Channel | Setup | Ships as | Admin needed | Cost | Mobile | Groups | Injection surface |
|---|---|---|---|---|---|---|---|
| **Discord** | Medium — portal, intents, OAuth invite | Official plugin | None (own your server) | Free | Excellent native app | Guilds, channels, threads, forums | Medium — link embeds, other members, bot messages |
| **Slack** | Hard — app manifest + workspace install | Official plugin | **Yes**, usually | Free tier truncates history | Good | Channels, MPIMs, threads | Medium-high — unfurls, integrations, coworkers |
| **Telegram** | Trivial — one bot token | **Core** | None | Free | Excellent | Groups, forum topics | Medium — link previews on by default |
| **WhatsApp** | Medium — QR pairing, heavy state | Official plugin | None | Free | Excellent | Groups | High — everyone has your number |
| **Signal** | Medium — `signal-cli` daemon | Official plugin | None | Free | Good | Groups | Low — closed contact graph |
| **iMessage** | Medium — Full Disk Access, `imsg`, SIP for extras | **Core** | None | Free | Excellent (Apple only) | Group chats | Medium — anyone can text you |
| **SMS (Twilio)** | Hard — needs a **public HTTPS** webhook | Official plugin | None | Per-message | Universal | None (DM only) | High — text only, unauthenticated senders |
| **Voice call** | Hard — public webhook + carrier | Official plugin | None | Per-minute | Any phone | N/A | High |
| **Microsoft Teams** | Hard — Bot Framework registration | Official plugin | **Yes** | Enterprise | Fine | Yes | Medium-high |
| **Matrix** | Medium | Official plugin | None if self-hosted | Free | Mediocre | Rooms | Low |
| **WebChat** | Zero | **Core** | None | Free | Browser only | No | Lowest |

### The recommendation, and why

**Discord for personal. Slack only if your team already lives there.**

The reasoning, in order of weight:

1. **You own the trust boundary.** A private Discord server where the only two members are you and
   your bot means guild policy, channel allowlists, and mention gating are all belt-and-braces on
   top of a room nobody else can enter. You cannot get that in a Slack workspace you don't
   administer.
2. **No approval queue.** Slack app installs need `Install App → Install to Workspace`, which on
   most managed workspaces means filing a request and waiting. Discord needs nobody's permission.
3. **Purpose-scoped channels are free context separation.** Each Discord channel gets its own
   session key (`agent:main:discord:channel:<id>`), so `#dev` chatter never contaminates `#brief`.
   Slack does this too, but you can't freely create channels in someone else's workspace.
4. **Threads, slash commands, reactions, voice channels, file attachments** — Discord's plugin
   surface is one of the most complete in OpenClaw, including realtime voice channel conversations.

Telegram genuinely is faster to set up (core install, one BotFather token, no plugin, no restart).
If you want a working assistant in ten minutes tonight, use Telegram and migrate later — channels
are additive and nothing you configure is wasted. The reason this plan picks Discord anyway is the
channel topology in §2.3, which Telegram's flat group model doesn't reproduce as cleanly.

---

## 2. Discord — the complete walkthrough

### 2.1 Create the application

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** in the sidebar → set **Username** to your agent's name
3. **Privileged Gateway Intents** — this is where most setups fail:

   | Intent | Status | Needed for |
   |---|---|---|
   | **Message Content** | **Required** | The bot literally cannot read message text without it |
   | **Server Members** | Recommended | Role allowlists, name→ID matching, channel-audience access groups |
   | **Presence** | Optional | Only `guilds.<id>.presenceEvents`. Skip it. |

4. **Reset Token** → copy it. Despite the name, this generates your *first* token; nothing is being
   reset.
5. **OAuth2 → URL Generator** → scopes: `bot` and `applications.commands`
6. Bot Permissions:

   | Group | Permission | Why |
   |---|---|---|
   | General | View Channels | See the channels at all |
   | Text | Send Messages | Reply |
   | Text | Read Message History | Context on inbound turns |
   | Text | Embed Links | Rich replies |
   | Text | Attach Files | Send images/files |
   | Text | Add Reactions | Optional — ack reactions, approval prompts |
   | Text | **Send Messages in Threads** | **Required** if you use threads or forum channels |

   Grant nothing beyond this. No Manage Server, no Manage Messages, no Administrator.

7. Open the generated URL, select your **private server** (Create My Own → For me and my friends),
   **Continue**.

### 2.2 The step the README skips: allow DMs from server members

Pairing delivers the approval code **as a DM from the bot**. If your server blocks member DMs, the
code never arrives and pairing looks broken.

Right-click your **server icon** → **Privacy Settings** → toggle **Direct Messages** on.

You can turn it back off after pairing if you only ever use guild channels.

### 2.3 Channel topology — why purpose-scoped channels matter

Every Discord channel maps to its own OpenClaw session key:

```
agent:main:discord:channel:1234567890          # a channel
agent:main:discord:channel:1234567890:thread:9876   # a thread inside it
agent:main:main                                 # DMs (under default dmScope)
```

Session key = context bucket = concurrency unit. So the topology *is* the context architecture:

| Channel | Purpose | Session behaviour |
|---|---|---|
| `#assistant` | General chat | Long-lived working session |
| `#brief` | Morning brief, evening review, heartbeat target | Write-mostly; cron posts here with `--session isolated` so each run costs ~2–5K tokens instead of ~100K |
| `#inbox` | `triage` output — email summaries, drafts | Untrusted content lands here as *data*. Never bound to an agent with `exec`. |
| `#dev` | CI, PRs, build failures | Bind to the `dev` agent (§4). Sandboxed, no personal memory. |
| `#capture` | Quick notes → Notion/Obsidian | Short bursts; the 22:00 capture-sweep cron reads it |

Two consequences worth internalising:

- **Cost.** A single mega-channel means every turn drags the whole day's transcript through the
  model. Five scoped channels means five small contexts.
- **Blast radius.** `#inbox` carrying prompt-injected email text cannot reach `#dev`'s tools,
  because they're different sessions and (per §4) different agents.

> **Nuance the README doesn't mention:** `MEMORY.md` auto-loads **only in DM sessions**. Guild
> channels do not load it. If you want long-term memory available in `#assistant`, either talk to
> the bot in DMs, or put the stable stuff in `AGENTS.md`/`USER.md` (injected into every session)
> and tell the agent to reach for `memory_search` / `memory_get` on demand in channels.

### 2.4 Getting the IDs

**User Settings** (gear) → **Developer** → toggle **Developer Mode** on. On mobile: **App
Settings** → **Advanced**.

| ID | How |
|---|---|
| Server (guild) ID | Right-click server icon → **Copy Server ID** |
| Your user ID | Right-click your own avatar → **Copy User ID** |
| Channel ID | Right-click the channel → **Copy Channel ID** |

Use IDs everywhere. Names and slugs technically work for some fields but require
`dangerouslyAllowNameMatching: true`, and `openclaw security audit` warns when it finds them.

### 2.5 Locking it to one user

```json5
{
  channels: {
    discord: {
      enabled: true,
      token: { source: "env", provider: "default", id: "DISCORD_BOT_TOKEN" },

      // DMs: unknown senders get an 8-char code and go nowhere until you approve.
      dmPolicy: "pairing",

      // Guilds: only servers listed below. This is also the runtime fallback
      // if channels.discord exists at all, but be explicit.
      groupPolicy: "allowlist",

      // Bot-authored inbound is ignored by default. Keep it that way.
      allowBots: false,

      // Outbound URLs do NOT expand into link previews. Default is already true;
      // set it explicitly so nobody "helpfully" flips it later.
      suppressEmbeds: true,

      guilds: {
        YOUR_SERVER_ID: {
          requireMention: false,      // fine on a server that is only you
          ignoreOtherMentions: true,  // drop msgs that @ someone else, not the bot
          users: ["YOUR_USER_ID"],    // sender allowlist
        },
      },
    },
  },
}
```

Apply it without hand-editing the live config:

```bash
export DISCORD_BOT_TOKEN="..."
openclaw config patch --file ./discord.patch.json5 --dry-run
openclaw config patch --file ./discord.patch.json5
openclaw gateway restart
```

If OpenClaw runs as a managed service, run `openclaw gateway install` from a shell where
`DISCORD_BOT_TOKEN` is set, or put it in `~/.openclaw/.env` so the daemon can resolve the SecretRef
after restart. `DISCORD_BOT_TOKEN` only applies to the **default** account; named accounts must
carry their own `token`.

**DM policy values** (identical across every channel):

| Value | Behaviour |
|---|---|
| `pairing` | **Default.** Unknown sender gets a code; message is held until approved |
| `allowlist` | Only `allowFrom` senders. Requires at least one entry |
| `open` | Anyone. Requires `allowFrom` to literally contain `"*"` |
| `disabled` | No DMs at all |

Pairing codes are 8 uppercase characters with `0O1I` excluded, expire after **1 hour**, and cap at
**3 pending requests** per channel account.

```bash
openclaw pairing list discord
openclaw pairing approve discord <CODE> --notify
```

### 2.6 The guild-channel-map trap

This one bites everybody. A guild entry with **no** `channels` map lets the bot work in every
channel it can see. **Adding even one channel entry converts the map into an allowlist** — every
channel you didn't list is now denied, not merely left at guild defaults. The symptom is "my bot
went silent everywhere except the one channel I was configuring."

Use the `"*"` wildcard to keep the rest of the guild reachable:

```json5
{
  channels: {
    discord: {
      guilds: {
        YOUR_SERVER_ID: {
          requireMention: true,
          users: ["YOUR_USER_ID"],
          channels: {
            // #assistant: always-on, no mention needed
            ASSISTANT_CHANNEL_ID: { enabled: true, requireMention: false },
            // #dev: mention-gated
            DEV_CHANNEL_ID: { enabled: true, requireMention: true },
            // everything else keeps guild defaults
            "*": { enabled: true, requireMention: true },
          },
        },
      },
    },
  },
}
```

Channel entries override guild-level values. A channel entry with `users: ["*"]` opens that one
room to any sender even when the guild `users` list is narrow — so don't write that unless you mean
it. Threads fall back to their parent channel's entry.

### 2.7 Threads

Threads append `:thread:<threadId>` to the base session key, so a thread is a genuinely separate
context from its parent channel. Useful for one-off deep dives that you don't want polluting
`#assistant`.

```json5
{
  channels: {
    discord: {
      threadBindings: {
        enabled: true,        // /focus, /unfocus, /agents, /session idle, /session max-age
        idleHours: 24,        // auto-unfocus after inactivity; 0 disables
        maxAgeHours: 168,     // hard max age; 0 disables
        spawnSessions: true,  // sessions_spawn({ thread: true }) creates + binds a thread
      },
    },
  },
}
```

Requires the **Send Messages in Threads** permission from §2.1.

### 2.8 Slash commands

`commands.native` defaults to `"auto"` and is **enabled for Discord** — the plugin registers slash
commands at startup and cleans them up on shutdown.

```json5
{
  channels: {
    discord: {
      commands: { native: true },
      slashCommand: { ephemeral: true },   // default: replies only you can see
    },
  },
}
```

Two things to know:

- Commands may still be **visible** in the Discord UI to unauthorized users; execution enforces
  OpenClaw's allowlists and replies "not authorized". Visibility is not authorization.
- Setting `commands.native: false` skips registration but **does not remove** already-registered
  commands — they linger in Discord until you delete them from the app.

### 2.9 Verification

```bash
openclaw status
openclaw gateway status                 # expect 127.0.0.1:18789
openclaw doctor
openclaw channels status --probe        # transport connected + permission audit
openclaw channels capabilities --channel discord --target channel:<CHANNEL_ID>
openclaw pairing list discord
openclaw security audit
```

Healthy baseline from `channels status --probe`: `Runtime: running`, `Connectivity probe: ok`,
capability reported as `read-only` / `write-capable` / `admin-capable`, and the channel probe
showing transport connected plus `works` or `audit ok`.

Note: permission checks in `--probe` **only work for numeric channel IDs**. Slug keys may still
route at runtime, but probe can't verify permissions for them. Another reason to use IDs.

### 2.10 Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Bot online, no guild replies | `openclaw channels status --probe` | Enable Message Content Intent; verify guild allowlist; restart Gateway after intent changes |
| Silent in channels that used to work | Did the guild entry gain a `channels` map? | It's now an allowlist — add a `"*"` entry (§2.6) |
| `requireMention: false` but still blocked | `groupPolicy`, where `requireMention` is declared, guild/channel `users` | `requireMention` must live under `channels.discord.guilds.<id>` or a channel entry, not at the channel root |
| DM replies missing | `openclaw pairing list discord` | Approve the code, or check `dm.enabled` / `dmPolicy` |
| Pairing code never arrives | Server → Privacy Settings | Enable **Direct Messages** (§2.2) |
| Typing + token usage but no message | Gateway verbose log for suppressed final payload | You're in an ambient-room-event or `visibleReplies: "message_tool"` mode and the model never called `message(action=send)`. Keep `messages.groupChat.visibleReplies: "automatic"` for normal use |
| Startup blocked/rate-limited by Discord | Application lookup REST call | Set `channels.discord.applicationId` so startup can skip it |
| Duplicate replies / stuck sessions | Logs for `Slow listener detected`, `stuck session: ... state=processing` | Discord applies no channel-owned timeout to queued turns; investigate the tool/runtime lifecycle |

```bash
openclaw logs --follow
openclaw doctor --fix       # migrates legacy keys (dm.policy → dmPolicy, allow → enabled, etc.)
openclaw gateway restart
```

---

## 3. Slack — the walkthrough the README defers

Slack is genuinely harder than Discord, and most of the difficulty is organisational rather than
technical. Budget an afternoon, and check whether you can install apps in your workspace *before*
you build anything.

### 3.1 Install the plugin

```bash
openclaw plugins install @openclaw/slack
```

This registers and enables the plugin. It does nothing until the Slack app exists and channel
config is written.

### 3.2 Socket Mode vs HTTP Request URLs — pick Socket Mode

The two transports reach **feature parity** for messaging, slash commands, App Home, and
interactivity. Pick by deployment shape, not features.

| Concern | Socket Mode (default) | HTTP Request URLs |
|---|---|---|
| Public Gateway URL | Not required | **Required** (DNS, TLS, reverse proxy or tunnel) |
| Network direction | Outbound WSS to `wss-primary.slack.com` | Inbound HTTPS only |
| Tokens | Bot token + App-Level Token (`connections:write`) | Bot token + Signing Secret |
| Dev laptop / firewall | Works as-is | Needs ngrok / Cloudflare Tunnel / Tailscale Funnel |
| Horizontal scaling | One session per app per host | Stateless; replicas behind a load balancer |
| Slash command transport | Over the WS; `slash_commands[].url` is ignored | Slack POSTs to `slash_commands[].url`; the field is **required** |
| Request signing | Not used — auth is the App-Level Token | Slack signs every request; verified with `signingSecret` |

**For this setup, Socket Mode is not a preference — it's the only sane option.** Our Gateway is
`bind: "loopback"` on `127.0.0.1:18789`. HTTP Request URLs would require punching a publicly
resolvable HTTPS endpoint through to that port, which throws away the entire Phase 1 hardening
posture. Socket Mode dials *out* to Slack and needs no inbound path at all.

One warning that matters if you ever run two Gateways: Slack can hold multiple Socket Mode
connections for one app and may deliver a payload to **any** of them. Two Gateways sharing one
Slack app need identical routing and authorization config, or you use a separate Slack app per
Gateway.

### 3.3 Create the app from a manifest

[api.slack.com/apps](https://api.slack.com/apps/new) → **Create New App** → **From a manifest** →
select your workspace → paste → **Next** → **Create**.

The **recommended** manifest, verbatim from the OpenClaw docs:

```json
{
  "display_information": {
    "name": "OpenClaw",
    "description": "Slack connector for OpenClaw"
  },
  "features": {
    "bot_user": { "display_name": "OpenClaw", "always_online": true },
    "app_home": {
      "home_tab_enabled": true,
      "messages_tab_enabled": true,
      "messages_tab_read_only_enabled": false
    },
    "agent_view": {
      "agent_description": "OpenClaw connects Slack Agent View conversations to OpenClaw agents.",
      "suggested_prompts": [
        { "title": "What can you do?", "message": "What can you help me with?" },
        { "title": "Summarize this channel", "message": "Summarize the recent activity in this channel." },
        { "title": "Draft a reply", "message": "Help me draft a reply." }
      ]
    },
    "slash_commands": [
      { "command": "/openclaw", "description": "Send a message to OpenClaw", "should_escape": false }
    ]
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "app_mentions:read",
        "assistant:write",
        "channels:history",
        "channels:read",
        "chat:write",
        "commands",
        "emoji:read",
        "files:read",
        "files:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "mpim:write",
        "pins:read",
        "pins:write",
        "reactions:read",
        "reactions:write",
        "usergroups:read",
        "users:read"
      ]
    }
  },
  "settings": {
    "socket_mode_enabled": true,
    "event_subscriptions": {
      "bot_events": [
        "app_home_opened",
        "app_mention",
        "app_context_changed",
        "channel_rename",
        "member_joined_channel",
        "member_left_channel",
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
        "pin_added",
        "pin_removed",
        "reaction_added",
        "reaction_removed"
      ]
    }
  }
}
```

**If your workspace policy restricts scopes**, OpenClaw ships a documented **minimal** scope set.
It covers DMs, channel/group history, mentions, and slash commands, and drops files, reactions,
pins, group DMs, emoji, and usergroups:

```json
"bot": [
  "app_mentions:read",
  "assistant:write",
  "channels:history",
  "channels:read",
  "chat:write",
  "commands",
  "groups:history",
  "groups:read",
  "im:history",
  "im:read",
  "im:write",
  "users:read"
]
```

with the reduced event set:

```json
"bot_events": [
  "app_home_opened",
  "app_mention",
  "app_context_changed",
  "message.channels",
  "message.groups",
  "message.im"
]
```

What the non-obvious scopes actually buy you:

| Scope | Needed for |
|---|---|
| `channels:read` / `groups:read` | Membership lookups via `conversations.members` — **required** for the `allowBots` owner-presence check |
| `usergroups:read` | User-group mentions (`<!subteam^S...>`) counting as a mention |
| `files:read` | Inbound attachments, and **Slack audio clips → voice input** |
| `reactions:write` | Ack reactions, typing fallback, approval prompts rendered as reactions |
| `assistant:write` + `app_context_changed` | Slack Agent View (each Agent View root gets its own thread session) |

For **work/dev use specifically**, the minimal set is usually enough and much easier to get
approved. Start there.

### 3.4 Tokens

After Slack creates the app:

- **Basic Information → App-Level Tokens → Generate Token and Scopes** → add `connections:write`,
  save, copy the **App-Level Token** (`xapp-…`).
- **Install App → Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`).

```bash
export SLACK_APP_TOKEN="xapp-..."
export SLACK_BOT_TOKEN="xoxb-..."

cat > slack.socket.patch.json5 <<'JSON5'
{
  channels: {
    slack: {
      enabled: true,
      mode: "socket",
      appToken: { source: "env", provider: "default", id: "SLACK_APP_TOKEN" },
      botToken: { source: "env", provider: "default", id: "SLACK_BOT_TOKEN" },
    },
  },
}
JSON5

openclaw config patch --file ./slack.socket.patch.json5 --dry-run
openclaw config patch --file ./slack.socket.patch.json5
openclaw gateway restart
```

Token model, so you don't guess:

- Bot identity (default): `botToken` + `appToken` for Socket Mode, or `botToken` + `signingSecret`
  for HTTP.
- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_USER_TOKEN` env fallbacks apply **only to the
  default account**.
- Config token values win over env fallback.
- User identity (`identity: "user"`, posting as a real person) uses `userToken` and defaults to
  read-only (`userTokenReadOnly: true`). Don't. A work bot posting as you is a bad idea.

### 3.5 Workspace-admin approval — the honest version

On a free or personal workspace you can install apps yourself and you're done in ten minutes.

On any managed workspace, expect:

- **App approval is usually on.** `Install to Workspace` files a request to workspace admins with
  the full scope list. A 24-scope request from an unknown app called "OpenClaw" gets scrutiny. This
  is the single strongest argument for submitting the **minimal** manifest.
- **Enterprise Grid is a different animal.** Org-wide installs need an Org Admin/Owner, use
  `enterpriseOrgInstall: true`, and are deliberately limited to `message` and `app_mention` events
  plus immediate replies. Relay mode, slash commands, interactions, App Home, reaction listeners,
  pins, Slack action tools, native approvals, bindings, and queued/scheduled delivery are all
  **unavailable** on an enterprise account. Enterprise also rejects pairing and per-user DM
  allowlists — DMs must be either `disabled` or `open` with `allowFrom: ["*"]`.
- **Data-governance policy may forbid it outright.** An app with `channels:history` on a workspace
  under a DLP regime is a conversation with your security team, not a config change. Have it before
  you build.

If the answer is no, run Slack in a **personal free workspace** you create yourself and invite
nothing but the bot. You lose team integration; you keep the dev-notification workflow.

### 3.6 Access control

```json5
{
  channels: {
    slack: {
      enabled: true,
      mode: "socket",
      appToken: { source: "env", provider: "default", id: "SLACK_APP_TOKEN" },
      botToken: { source: "env", provider: "default", id: "SLACK_BOT_TOKEN" },

      dmPolicy: "allowlist",
      allowFrom: ["U0123456789"],       // your Slack user ID

      groupPolicy: "allowlist",
      channels: {
        C0123456789: {                  // #dev — MUST be the channel ID
          enabled: true,
          requireMention: true,
          users: ["U0123456789"],
        },
      },

      // Link unfurling. Default is already false; be explicit.
      unfurlLinks: false,
      unfurlMedia: false,

      // Don't let a reply or thread participation silently bypass mention gating
      // in a shared work channel.
      implicitMentions: {
        replyToBot: true,
        threadParticipation: false,
      },

      dm: { groupEnabled: false },      // ignore multi-person DMs (default)
      dangerouslyAllowNameMatching: false,
    },
  },
}
```

**The failure mode that wastes an afternoon:** channel allowlist keys **must be stable Slack
channel IDs** (`C0123456789`), not names. Under `groupPolicy: "allowlist"`, a name-based key
(`"#eng-my-channel"`) will *never* route and every message in that channel is **silently blocked**
with no error. Under `groupPolicy: "open"` the same key appears to work, because the key isn't
consulted for routing — which is exactly why people get burned when they later tighten the policy.

Get the ID: right-click the channel in Slack → **Copy link** → the `C...` is at the end of the URL.
IDs must use Slack's canonical uppercase prefix; lowercase and short lookalikes fail startup.

Also: if `channels.slack` is **completely missing** (env-only setup), runtime falls back to
`groupPolicy: "allowlist"` and logs a warning — even if `channels.defaults.groupPolicy` says
otherwise. Write the block.

```bash
openclaw pairing approve slack <CODE>     # if you use dmPolicy: "pairing"
openclaw channels status --probe
```

### 3.7 Binding Slack to the `dev` agent

See §4 — this is not a `channels.slack` key.

---

## 4. Running both at once

### 4.1 Routing is deterministic

OpenClaw replies **back to the channel the message came from**. The model does not choose a
channel. Nothing you say to the assistant can make it answer your personal Discord DM into a work
Slack channel.

### 4.2 Agent binding — top-level `bindings[]`

> **Correction:** `channels.discord.agent` and `channels.slack.agent` are **not real config keys**.
> `openclaw.config.json5` in this repo currently uses `agent: "main"` inside `channels.discord` and
> a commented `agent: "dev"` inside the `slack` block. Neither does anything. Agent binding lives in
> a **top-level `bindings[]` array**.

```json5
{
  agents: {
    entries: {
      main: { /* ... */ },
      triage: { /* ... */ },
      dev: { /* ... */ },
    },
  },

  bindings: [
    // Discord #dev channel → dev agent (most specific first)
    {
      agentId: "dev",
      match: { channel: "discord", peer: { kind: "channel", id: "DEV_CHANNEL_ID" } },
    },

    // Everything else on Discord → main
    { agentId: "main", match: { channel: "discord", accountId: "*" } },

    // The entire work Slack workspace → dev
    { agentId: "dev", match: { channel: "slack", teamId: "T0123456789" } },
  ],
}
```

Matching runs in a fixed priority order — **first match wins**:

| # | Match | Example |
|---|---|---|
| 1 | Exact peer (`peer.kind` + `peer.id`) | one Discord channel |
| 2 | Parent peer (thread inheritance) | a thread inside that channel |
| 3 | Peer wildcard (`peer.id: "*"`) | all channels of a kind |
| 4 | Guild + roles (Discord) | `guildId` + `roles` |
| 5 | Guild (Discord) | `guildId` |
| 6 | Team (Slack) | `teamId` |
| 7 | Account | `accountId` |
| 8 | Channel | `accountId: "*"` |
| 9 | Default agent | `agents.entries.*.default`, else first entry, else `main` |

When a binding sets multiple match fields, **all of them must match**. The matched agent determines
which workspace and which session store are used — which is the whole point.

### 4.3 Session scoping

```json5
{ session: { dmScope: "per-channel-peer" } }
```

| Value | Behaviour |
|---|---|
| `main` | **Default.** All DMs from every channel collapse into one main session |
| `per-peer` | One session per sender, across channels |
| `per-channel-peer` | One session per (channel, sender) pair — **our setting** |
| `per-account-channel-peer` | Adds account to the key; maximum isolation |

Two things `dmScope` does *not* do:

- **It doesn't affect groups or channels.** Those are always isolated
  (`agent:<agentId>:<channel>:group:<id>` / `:channel:<id>`) regardless of scope.
- **It doesn't isolate agents.** Two channels bound to the same agent share a workspace and a memory
  store even under `per-account-channel-peer`. Isolation of *memory* comes from binding to a
  different agent, not from scoping sessions.

A binding can override scope for just its matched peers via `bindings[].session.dmScope`.

### 4.4 Why memory isolation actually holds

The three-agent split does the work, and `dmScope` is a refinement on top:

```
Discord DM + #assistant ──▶ main   workspace ~/.openclaw/workspace
                                    memory.search.rememberAcrossConversations: true
                                    exec: DENIED

Discord #inbox          ──▶ triage workspace ~/.openclaw/workspace-triage
                                    rememberAcrossConversations: false
                                    exec: DENIED, no outbound message tool

Slack workspace + #dev  ──▶ dev    workspace ~/code
                                    rememberAcrossConversations: false
                                    exec: ALLOWED, sandboxed
```

- Each `agentId` is an isolated workspace **and** an isolated session store:
  `~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`. Separate SQLite files, so there is no
  shared index for cross-conversation search to reach across.
- `dev` runs with `rememberAcrossConversations: false`, so nothing said in a work Slack channel is
  ever written into anything `main` can retrieve.
- Cross-conversation memory is restricted to **private** conversations anyway — group/channel
  sessions can't read cross-conversation transcripts.
- `per-channel-peer` then stops a personal Discord DM and a work Slack DM from merging into the
  same main session even in the case where they'd otherwise share an agent.

The thing that would break this is a single agent bound to both channels. Don't do that, and the
rest is automatic.

### 4.5 Shared defaults

Set the safe baseline once instead of per channel:

```json5
{
  channels: {
    defaults: {
      groupPolicy: "allowlist",
      contextVisibility: "allowlist_quote",   // only allowlisted senders' context reaches the model
      implicitMentions: {
        replyToBot: true,
        quotedBot: true,
        threadParticipation: true,
      },
      botLoopProtection: {
        enabled: true,
        maxEventsPerWindow: 20,
        windowSeconds: 60,
        cooldownSeconds: 60,
      },
      heartbeat: { showOk: false, showAlerts: true, useIndicator: true },
    },
  },
}
```

`contextVisibility` is underrated for shared work channels: `allowlist` includes only allowlisted
senders' quoted/thread/history context; `allowlist_quote` does the same but keeps explicit
quote/reply context. On a busy Slack channel this is the difference between "the agent read the
message you mentioned it in" and "the agent read fifty messages from coworkers, one of which
contained a link to a page with instructions in it."

Resolution order is account → channel → defaults, independently per flag.

---

## 5. Voice and mobile

### 5.1 macOS Hub app

Download the `.dmg` or `.zip` from [GitHub releases](https://github.com/openclaw/openclaw/releases).
It installs the matching CLI runtime on first run and sets up the Gateway in one of two modes:

| Mode | Meaning |
|---|---|
| **Local** (ours) | This Mac runs the Gateway and keeps it alive with launchd; the app owns it and keeps versions aligned |
| **Remote** | Another host runs the Gateway; this Mac controls it over SSH, LAN, or tailnet |

What you get: menu-bar tray UI, native notifications, **WebChat**, **Canvas**, voice input,
Mac-hosted node tools like `system.run`, and an embedded dashboard that opens external links in a
sidebar browser rather than your real browser.

**Quick Chat** is the Spotlight-style composer — **⌥-Space** by default, also reachable from the
menu-bar menu, and rebindable in Settings. In practice this ends up being how you use the assistant
at the desk, with Discord being how you use it away from it.

### 5.2 Voice wake and push-to-talk

| Feature | Requirement | Default |
|---|---|---|
| Push-to-talk | **Hold the right Option key** (keyCode 61 + `.option`) | Toggle: "Hold Right Option to talk" |
| Voice Wake | macOS **26+**, and Apple Speech must support **on-device** recognition for your language | Toggle: "Voice Wake" |

The wake-word recogniser waits for trigger words, requires a **0.55s** pause between the trigger and
your actual speech, and hard-stops at **120s** of continuous capture. If your language has no
on-device model, Voice Wake stays disabled — the app deliberately refuses network-only fallbacks for
passive listening, which is the correct call. Push-to-talk still works.

The global hotkey listener **observes but never intercepts** key events, so right-Option keeps
working normally in other apps.

### 5.3 macOS permissions — grant vs decline

| Permission | Enables | Verdict |
|---|---|---|
| **Microphone** | Any voice input at all | **Grant** if you want voice; otherwise decline |
| **Speech Recognition** | Transcribing that audio | **Grant** alongside Microphone |
| **Accessibility / Input Monitoring** | Detecting the right-Option global hotkey | **Grant only if you want push-to-talk.** This is the most powerful permission on the list — it can observe input system-wide. Voice Wake works without it |
| **Notifications** | Native alerts for replies and approvals | Grant — low risk, high utility |
| **Automation (AppleEvents)** | Driving other apps, iMessage sending | **Decline** unless you're doing iMessage or app automation |
| **Screen Recording** | Screen capture, screen-aware features, Canvas capture | **Decline.** Highest-consequence grant on the list. Add it later for a specific workflow |
| **Location** | Location-aware answers | Decline unless you want it |
| **Files & Folders** (Desktop/Documents/Downloads) | Filesystem tools | Decline; scope filesystem access through the MCP filesystem server instead (README Phase 5) |

macOS TCC grants persist per code-signed app identity. Grant the minimum, then add individually as
you find you need them.

### 5.4 iOS and Android companions — the loopback problem

Both apps are **nodes**: they connect to a Gateway, they don't run one. And here is the constraint
nobody mentions up front:

> **The default loopback bind is not reachable from a phone.**

Our Gateway is `bind: "loopback"` on `127.0.0.1:18789`. Your phone cannot reach that, full stop.
You have four honest options:

| Option | How | Cost | Verdict |
|---|---|---|---|
| **Tailscale Serve** | `openclaw gateway --port 18789 --tailscale serve`, or `gateway.tailscale.mode: "serve"` | Free tier is generous | **Recommended.** Keeps loopback bind, gives you `https://<magicdns>/` with TLS and tailnet-only reachability. Optional `gateway.auth.allowTailscale: true` uses Tailscale identity headers |
| **SSH tunnel** | `ssh -N -L 18789:127.0.0.1:18789 user@gateway-host` | Free | Fine for a laptop-to-laptop operator connection. Awkward on a phone |
| **`bind: "tailnet"`** | Direct tailnet IP, no Serve | Free | Works, but no HTTPS. Auth becomes **mandatory** |
| **Tailscale Funnel** | `gateway.tailscale.mode: "funnel"` + `gateway.auth.mode: "password"` | Free | **Public internet exposure of your personal agent.** Only if you genuinely need it. Requires Tailscale 1.38.3+, MagicDNS, HTTPS, and ports 443/8443/10000 |

Non-loopback binds **must** use Gateway authentication (token or password). This is enforced, and
it's the right default.

Pairing a phone, once the Gateway is reachable:

```bash
openclaw gateway --port 18789 --tailscale serve
# Control UI → Nodes → "Pair mobile device" → shows a QR + setup code
# iOS:     Settings → Gateway → scan QR
# Android: Connect tab → setup code, or manual host/port
openclaw devices list
openclaw devices approve <requestId>
openclaw nodes status
```

What the mobile apps give you:

- **iOS** — chat with text and voice, read-only offline cache of recent chats, queues up to **50**
  text messages while disconnected and flushes on reconnect, Canvas via `node.invoke`, camera,
  location, optional HealthKit summaries, Apple Watch relay through the phone.
- **Android** — same core plus durable offline queuing (messages journaled to disk before send),
  on-device speech recognition, voice notes, continuous "Talk" mode for realtime conversation,
  `camera.snap` / `camera.clip`, notification forwarding with allow/blocklists and quiet hours,
  Wear OS companion.

Android networking specifics: mDNS/NSD on a local network, or Tailscale via Wide-Area Bonjour.
Cleartext `ws://` works **only** on private LANs and localhost; anything remote must be `wss://`.

With multiple paired Gateways, only the **focused** Gateway owns device capabilities — the others
stay connected but can't issue camera, location, or notification commands.

> **The laptop constraint still applies.** Your phone reaching the Gateway over Tailscale doesn't
> help when the Mac is asleep. Everything in README §1 about `pmset`, AC power, and catch-up cron
> semantics governs mobile too. Wanting reliable phone access is the strongest argument for the
> Phase 9 VPS migration.

### 5.5 Voice inside channels

| Channel | Voice story |
|---|---|
| **Discord** | Best in class. Realtime **voice channels** (`voice.enabled`, `voice.autoJoin`, `voice.followUsers`, `voice.realtime`) plus voice-message attachments. Voice messages need `ffmpeg` and `ffprobe` on the Gateway host for OGG/Opus + waveform |
| **Slack** | Post an **audio clip** to the app — it downloads with the bot token and runs the shared transcription pipeline. Requires `files:read`. Slackbot's dictation mic is a Slack-owned feature that emits nothing to third-party apps; OpenClaw cannot receive it |
| **Telegram / WhatsApp / Signal** | Voice notes supported |
| **Voice Call plugin** | Actual phone calls via Twilio/Plivo/Telnyx. Needs a **public webhook URL** — carriers cannot reach loopback or private addresses. `openclaw voicecall expose` manages Tailscale serve/funnel for this |

---

## 6. Alternatives worth knowing

**Telegram — fastest, and it's not close.** In the **core install**: no plugin, no restart. One
`/newbot` to [@BotFather](https://t.me/BotFather), set `TELEGRAM_BOT_TOKEN`, done. Supports groups
with forum topics that each get their own session (`:topic:<topicId>`), voice notes, video notes,
locations, stickers, message streaming, and rich Bot API 10.2 formatting. Use numeric user IDs in
allowlists, never `@usernames`.

*Beats Discord when:* you want it working tonight, or you already live in Telegram. *Watch out:*
**link previews are ON by default** — set `channels.telegram.linkPreview: false`. Group privacy mode
must be disabled at BotFather for the bot to see group messages.

**WhatsApp — most natural, heaviest.** Official plugin, but the runtime ships outside the core npm
package and installs on demand:

```bash
openclaw plugins install clawhub:@openclaw/whatsapp
openclaw channels login --channel whatsapp     # QR pairing
```

Uses **Baileys** (WhatsApp Web), not the Business API. That means: an unofficial transport tied to
your personal number, credentials at `~/.openclaw/credentials/whatsapp/<accountId>/creds.json`,
meaningful on-disk state, periodic re-linking, and reconnect loops as a normal failure mode.

*Beats Discord when:* the people you want the assistant to interact with are already on WhatsApp,
or you want it in the app you check most. *Watch out:* your assistant now shares a phone number with
your actual identity, and anyone with your number can message it. `dmPolicy: "pairing"` is doing
serious work here. Not recommended as your first channel.

**iMessage — Mac-native, and it's in core.** Uses the `imsg` CLI over JSON-RPC on a signed-in Mac.
Needs **Full Disk Access** (to read `chat.db`) and **Automation** (to send). Reactions, edits,
unsends, threaded replies, effects, polls, and group management need **SIP disabled** — which is a
real security downgrade to your whole machine, not a checkbox.

```bash
brew install steipete/tap/imsg
imsg rpc --help
```

*Beats Discord when:* you're all-Apple and want to text your assistant the way you text people, with
zero new apps. *Watch out:* Full Disk Access to the Messages database is a big grant, SIP-disable is
a bigger one, and BlueBubbles support has been **removed** in favour of `imsg`.

**SMS (Twilio) and voice call — the "no smartphone required" tier.** Both are official plugins and
both need a **public HTTPS endpoint** into the Gateway, which conflicts with our loopback posture
unless you front it with a tunnel. SMS is text-only, no MMS, DMs only, Twilio signature validation
on by default, default webhook path `/webhooks/sms`. Voice call supports notify mode (deliver a
message and hang up) and conversation mode.

*Beats Discord when:* you need the assistant to reach you where there is no data connection, or you
want it to actually phone you about something genuinely urgent. *Watch out:* per-message and
per-minute costs, and an unauthenticated global sender pool — `dmPolicy: "pairing"` is mandatory,
never `open`. One Twilio number can serve both SMS and Voice, but the webhooks are configured
separately and use separate Gateway paths.

**Signal** is worth a mention: `signal-cli`-backed, privacy-focused, closed contact graph, lowest
injection surface of any mainstream channel. Slower to set up than Telegram, but the best choice if
the content of your assistant conversations is itself sensitive.

---

## 7. Channel security checklist

Run through this for **every** channel you enable, not just the first one.

**Access**

- [ ] `dmPolicy` is `pairing` (personal) or `allowlist` (work). Never `open` — and note `open`
      requires `allowFrom` to literally contain `"*"`, which should feel like a speed bump
- [ ] `allowFrom` contains **stable numeric/prefixed IDs**, never usernames, display names, or emails
- [ ] `dangerouslyAllowNameMatching` is absent or `false` on every channel
- [ ] Pending pairing requests reviewed: `openclaw pairing list <channel>`
- [ ] Group DMs off: `dm.groupEnabled: false` (the default)

**Groups and channels**

- [ ] `groupPolicy: "allowlist"` everywhere; set `channels.defaults.groupPolicy` as the floor
- [ ] Guild/channel allowlist keys use **IDs**, not names (Slack name keys fail *silently*)
- [ ] If a Discord guild has a `channels` map, it contains a `"*"` entry — or you deliberately want
      every unlisted channel denied
- [ ] `requireMention: true` in any room with other humans in it
- [ ] `ignoreOtherMentions: true` in shared rooms — drops messages that @ someone else, not the bot
- [ ] `implicitMentions.threadParticipation: false` on shared work channels, so the bot doesn't stay
      "activated" in a thread after one legitimate mention
- [ ] `contextVisibility: "allowlist_quote"` on shared channels

**Injection surface**

- [ ] **Link previews off in OpenClaw config** — the key differs per channel:
      Discord `suppressEmbeds: true` (already the default), Slack `unfurlLinks: false` +
      `unfurlMedia: false` (already the default), Telegram `linkPreview: false` (**default is ON**)
- [ ] **Link previews off in the client too** — Discord Settings → Chat → Embeds and Link Previews;
      Slack Preferences → Messages & Media → hide previews
- [ ] Untrusted-content channels (`#inbox`, anything fed by email or the web) are bound to `triage`,
      which has `exec: deny` and no outbound message tool
- [ ] No channel that receives external content is bound to an agent with `exec: allow`

**Bots**

- [ ] `allowBots` is `false` (default) or `"mentions"`. Plain `true` accepts every bot in the room
- [ ] Bot loop protection left enabled — supported on **Discord, Feishu, Google Chat, Matrix, and
      Slack**; defaults are 20 events / 60s window / 60s cooldown per bot pair
- [ ] Slack `allowBots` owner-presence check can resolve members — needs `channels:read` for public
      channels and `groups:read` for private ones, or the message is dropped

**Binding and isolation**

- [ ] Every channel routes through an explicit `bindings[]` entry — not the `agents.entries.*.default`
      fallback, and definitely not a nonexistent `channels.<x>.agent` key
- [ ] Work channels bind to `dev`; personal channels bind to `main`; untrusted feeds bind to `triage`
- [ ] `session.dmScope: "per-channel-peer"` (or tighter)
- [ ] Work-bound agents have `memory.search.rememberAcrossConversations: false`

**Secrets and operations**

- [ ] Tokens come from `env` SecretRefs, never inline in config. `~/.openclaw/.env` for managed
      service installs
- [ ] `openclaw security audit` clean after every channel addition
- [ ] `openclaw channels status --probe` green for every enabled channel
- [ ] `openclaw doctor --fix` run after every OpenClaw update — it migrates legacy channel keys and
      clears stale plugin dependency symlinks

---

## Config keys the README gets wrong

Three fixes to fold into `openclaw.config.json5`. This document does not modify that file.

| Where | Currently | Should be |
|---|---|---|
| `channels.discord.linkPreviews: false` | Not a real key — silently ignored | `channels.discord.suppressEmbeds: true` (already the default). Slack's equivalent is `unfurlLinks: false` / `unfurlMedia: false`; Telegram's is `linkPreview: false` |
| `channels.discord.agent: "main"` | Not a real key — silently ignored | Top-level `bindings: [{ agentId: "main", match: { channel: "discord", accountId: "*" } }]` |
| `channels.slack.agent: "dev"` (commented) | Same problem | `bindings: [{ agentId: "dev", match: { channel: "slack", teamId: "T..." } }]` |

Plus two additions worth making:

- Discord is an **official plugin** — the setup needs `openclaw plugins install @openclaw/discord`
  (or `openclaw channels add --channel discord`) and a Gateway restart before `channels.discord`
  does anything.
- Slack needs `mode: "socket"`, `appToken`, and `botToken` — the commented block only has a single
  `token` key, which isn't the Slack schema.

Also worth noting rather than fixing: `channels.discord.enabled: true` alone will not make the bot
respond in guild channels. It will respond only to DMs, and only after pairing approval. The guild
allowlist is what turns it into a workspace.

---

## Sources

- [Chat channels index](https://docs.openclaw.ai/channels) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/index.md)
- [Discord channel](https://docs.openclaw.ai/channels/discord) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/discord.md)
- [Slack channel](https://docs.openclaw.ai/channels/slack) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/slack.md)
- [Telegram channel](https://docs.openclaw.ai/channels/telegram) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/telegram.md)
- [WhatsApp channel](https://docs.openclaw.ai/channels/whatsapp) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/whatsapp.md)
- [iMessage channel](https://docs.openclaw.ai/channels/imessage) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/imessage.md)
- [SMS channel](https://docs.openclaw.ai/channels/sms) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/sms.md)
- [Voice call CLI](https://docs.openclaw.ai/cli/voicecall) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/cli/voicecall.md)
- [Channel routing](https://docs.openclaw.ai/channels/channel-routing) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/channel-routing.md)
- [Groups](https://docs.openclaw.ai/channels/groups) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/groups.md)
- [Pairing](https://docs.openclaw.ai/channels/pairing) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/pairing.md)
- [Access groups](https://docs.openclaw.ai/channels/access-groups) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/access-groups.md)
- [Bot loop protection](https://docs.openclaw.ai/channels/bot-loop-protection) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/bot-loop-protection.md)
- [Channel troubleshooting](https://docs.openclaw.ai/channels/troubleshooting) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/channels/troubleshooting.md)
- [Configuration — channels](https://docs.openclaw.ai/gateway/config-channels) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/gateway/config-channels.md)
- [Remote access](https://docs.openclaw.ai/gateway/remote) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/gateway/remote.md)
- [Tailscale](https://docs.openclaw.ai/gateway/tailscale) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/gateway/tailscale.md)
- [macOS platform](https://docs.openclaw.ai/platforms/macos) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/platforms/macos.md)
- [macOS voice wake](https://docs.openclaw.ai/platforms/mac/voicewake) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/platforms/mac/voicewake.md)
- [iOS platform](https://docs.openclaw.ai/platforms/ios) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/platforms/ios.md)
- [Android platform](https://docs.openclaw.ai/platforms/android) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/platforms/android.md)
- [Plugins](https://docs.openclaw.ai/tools/plugin) — [raw](https://raw.githubusercontent.com/openclaw/openclaw/main/docs/tools/plugin.md)
- [Discord Developer Portal](https://discord.com/developers/applications)
- [Slack — create an app](https://api.slack.com/apps/new)
- [Slack — using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)
- [OpenClaw on GitHub](https://github.com/openclaw/openclaw)
