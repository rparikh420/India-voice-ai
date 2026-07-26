---
name: deploy-runbook
description: Deploy the Gujarati LiveKit voice agent — preflight, tests, image build, rollout, verify, rollback. Template for encoding any project's deploy.
metadata:
  {
    "openclaw":
      {
        "emoji": "🚢",
        "requires": { "bins": ["docker", "git"] },
      },
  }
---

# Deploy runbook — Gujarati Voice AI agent

**This is a worked example, not a general-purpose skill.** It encodes one
specific repo's deploy so the steps stop living in someone's head. Copy the
*shape* for your own projects; the commands below only apply to
`rparikh420/India-voice-ai`. See "Adapting this" at the bottom.

Only `dev` runs this. `dev` has `exec` allowed inside a Docker sandbox and no
access to email, calendar, or personal memory. `main` cannot execute and must
delegate here — that is deliberate, not an inconvenience to route around.

## What this thing is

A Python LiveKit voice agent: Sarvam STT/TTS (`saaras:v3` / `bulbul:v3`), OpenAI
`gpt-4o` for reasoning and tool calls, Silero VAD plus a multilingual turn
detector, LiveKit BVC noise cancellation. Tools are in-process mocks. It runs as
a **LiveKit worker**, not a web service — it registers with LiveKit and waits for
rooms. There is no port to curl and no health endpoint. That shapes every
verification step below.

| Fact | Value |
|---|---|
| Repo root | `/home/user/India-voice-ai` (flat: `agent.py` is at the root) |
| Entrypoint | `agent.py` — modes `dev`, `console`, `start`, `download-files` |
| Runtime | Python 3.10+ local; `python:3.12-slim` in the image |
| Tests | `pytest tests/ -v` (`pytest.ini`: `asyncio_mode=auto`) |
| Image | built from `Dockerfile`, `CMD ["python", "agent.py", "start"]` |
| Compose | `livekit-server` (7880/7881) + `voice-agent` |
| Secrets | `.env`, gitignored, from `.env.example` |
| CI | **none** — there is no `.github/`. Tests are a manual gate. |

> `docs/07-deployment.md` still says to `cd research` first. The repo has since
> been flattened. Trust the paths in this file; the docs are stale on that point.

## Preflight — never skip

```bash
cd /home/user/India-voice-ai
git status --porcelain          # must be empty
git rev-parse --short HEAD      # record this; it's your rollback target
git log --oneline -5
```

Then confirm the required env is actually populated. These five are hard
requirements; the agent starts and then fails on first room join without them:

```bash
grep -E '^(LIVEKIT_URL|LIVEKIT_API_KEY|LIVEKIT_API_SECRET|SARVAM_API_KEY|OPENAI_API_KEY)=' .env \
  | sed 's/=.*/=<set>/'
```

Never print the values. Never paste `.env` into chat, a PR, or an issue. If a
key is missing, stop and report which one — do not proceed with a placeholder.

Stop and ask your user before continuing if: the tree is dirty, you're not on
the branch they named, or `.env` is missing a key.

## 1. Test

```bash
python -m pytest tests/ -v
```

Covers `test_tagged_speech.py` (the `<NoInterrupt>`/`<Mute>` parser),
`test_tools.py` (mock tools), `test_orchestrator.py` (LangGraph flow). All must
pass. There is no CI to catch this later — this run *is* the gate.

If a test fails: stop, report the failing test and its output verbatim, do not
build. Do not "fix" a failing test as part of a deploy.

## 2. Build

```bash
GIT_SHA=$(git rev-parse --short HEAD)
docker build -t gujarati-voice-agent:$GIT_SHA -t gujarati-voice-agent:latest .
```

Always tag with the SHA as well as `latest`. `latest` alone gives you nothing to
roll back to, and this project has no registry retention policy to save you.

The build installs `gcc`, `libxml2-dev`, `libxslt1-dev` for `lxml`. If it fails
in the `pip install` layer, it is nearly always `lxml` or a `livekit-agents`
extra — report the actual pip error, don't guess.

## 3. Roll out

**Local / self-hosted LiveKit** (both services, mocks only):

```bash
docker compose up -d --build
docker compose ps
```

**Production** — LiveKit Cloud handles WebRTC; only the agent is yours:

```bash
docker run -d \
  --name voice-agent \
  --env-file .env \
  --restart unless-stopped \
  gujarati-voice-agent:$GIT_SHA
```

`LIVEKIT_URL` must be the `wss://…livekit.cloud` URL in production, not the
compose-internal `ws://livekit-server:7880`.

Workers scale horizontally: run N containers and LiveKit load-balances. This
project uses default worker registration (no `agent_name`), so any joining
participant gets an agent automatically. If you ever add `agent_name`, you must
also add matching `RoomAgentDispatch` on join tokens or nothing will connect —
and the failure looks like silence, not an error.

## 4. Verify

There is no health endpoint. Verification is log-shaped:

```bash
docker logs -f --tail 100 voice-agent
```

| Look for | Meaning |
|---|---|
| Worker registration line to the LiveKit URL | Connected. This is the minimum bar. |
| `session_started` | A real user joined |
| `session_closed` | Clean exit, with duration and transcript count |
| `tool_*` (e.g. `tool_book_appointment`) | Mock tool fired |
| Repeated restarts | Almost always a bad or missing key — check `.env` first |

Logs are structured JSON at `LOG_LEVEL=INFO`. A container that stays up but
never registers is the deceptive failure: it looks healthy to Docker and serves
nobody. Check for the registration line explicitly; don't infer health from
`docker ps`.

Then do one real call. `python agent.py console` speaks to it from a terminal
mic; the browser path is `python serve_dev_ui.py` on `http://127.0.0.1:8765`.
A deploy is not verified until audio has gone in and Gujarati has come out.

## 5. Rollback

```bash
docker stop voice-agent && docker rm voice-agent
docker run -d --name voice-agent --env-file .env --restart unless-stopped \
  gujarati-voice-agent:<previous-sha>
```

That is why step 2 tags by SHA. If the previous image was pruned, rebuild it:
`git stash && git checkout <previous-sha> && docker build -t gujarati-voice-agent:<previous-sha> .`

Roll back first, diagnose after. Do not debug a broken deploy in place while
calls are being dropped.

## Pre-production checklist

Things easy to miss on this specific project:

- `livekit.yaml` has `use_external_ip: false`. Behind a load balancer or on a
  cloud VM with a public IP, that must become `true` or media will not flow —
  signalling succeeds, audio doesn't, and the symptom is a silent call.
- RTC needs UDP `50000–60000` plus TCP `7881` open. Security groups routinely
  block these, with the same silent-call symptom.
- `LOG_BOOKING_EMAIL_ADDRESSES=false` masks patient email addresses in logs. On
  by default. For a real clinic pilot with real patients, turn it off.
- Resend (`RESEND_API_KEY`, `BOOKING_EMAIL_FROM`) is optional. Unconfigured,
  booking still succeeds and reports `confirmation_email=skipped_not_configured`.
  Confirm that's intended before shipping to a client.
- `python agent.py download-files` fetches VAD and turn-detector model files.
  The Dockerfile does not run it — first container start pays that cost.
- `AGENT_TIMEZONE` defaults to `Asia/Kolkata` and feeds date handling in the
  system prompt. Wrong timezone produces confidently wrong appointment dates.

## Confirmation gates

Do without asking: preflight, tests, build, reading logs, local compose runs.

Ask first, every time: deploying to anything real users touch, `git push`,
opening or merging a PR, tagging a release, changing `.env`, deleting images or
volumes, or anything that touches a running production container.

Report what you actually did, including which step failed if one did. Never
report a verified deploy you didn't verify.

## Adapting this

This is the template. For a new project, replace the contents but keep the
sections — that ordering is what makes a runbook usable at 2am:

1. **What this thing is** — the two or three architectural facts that explain
   why the rest of the steps look the way they do (here: it's a worker, so
   there's nothing to curl).
2. **Preflight** — clean tree, recorded rollback target, secrets present.
3. **Test** — the exact command, and an explicit "stop here on failure".
4. **Build** — immutable SHA tag, not just `latest`.
5. **Roll out** — the real command, with the env differences called out.
6. **Verify** — what *specifically* proves it works, including the deceptive
   failure that looks healthy.
7. **Rollback** — written down *before* you need it.
8. **Checklist** — the project-specific things that bite. Add one every time
   something bites you.
9. **Gates** — what the agent may do alone and what needs a human yes.

Keep it in the repo it describes, next to the code, so it goes stale visibly
rather than silently. Use `{baseDir}` if you bundle scripts alongside `SKILL.md`.
