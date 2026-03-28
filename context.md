# Research voice agent — handoff context

**Purpose:** Onboard new agents or humans quickly: what this folder is, how to run it, Patel Dental demo behavior, **Resend booking email** (primary + fallback), troubleshooting, and **conversation-derived** decisions. **Read this file first** when switching agents or picking up the repo.

**Last updated:** 2026-03-26 (agent-switch handoff: dental demo, Resend pipeline, `BOOKING_EMAIL_TO` fallback, prompts, tests, logging).

---

## What this project is

- **Gujarati (Indian) voice agent** on **LiveKit** (WebRTC), **Sarvam** STT/TTS (`gu-IN`, speaker **`rahul`**, `bulbul:v3`), **OpenAI GPT-4o**, Silero VAD + multilingual turn detection, BVC noise cancellation.
- **Demo persona:** **Patel Dental Clinic** (`CLINIC_NAME` in `config.py`, default). System prompt in `agent.py` is dental / appointment oriented (Gujarati).
- **Tools:** Mostly **mock** stubs in `tools.py` (`lookup_customer`, `book_appointment`, `check_availability`, `cancel_appointment`, `simulate_workflow`) for CRM/slots/booking **state**. **Exception:** `book_appointment` can call **real Resend** HTTPS (see `booking_email.py`) when env is set.
- **LangGraph** `orchestrator.py` — multi-step experiments only; **`execute_action` does not send Resend** and is **not** wired into `agent.py`. Voice path = GPT-4o + `ALL_TOOLS` only.

---

## How to run (local)

1. `cd research && cp .env.example .env` — set `LIVEKIT_*`, `SARVAM_API_KEY`, `OPENAI_API_KEY`; optional Resend vars below.
2. **Agent:** `.venv/bin/python agent.py dev` from `research/` (ensure worker registers to your LiveKit project).
3. **Browser UI:** `.venv/bin/python serve_dev_ui.py` → `http://127.0.0.1:8765` — token + URL from `.env`; click **Connect**. Do **not** rely on `file://` + ad-hoc JWT URL extraction for Python-minted tokens.
4. **Alt:** `frontend/index.html` with manual token, or `../frontend` Next.js per docs.

**Docker:** `docker-compose.yml` is LiveKit + voice-agent only (no n8n/Postgres in this design).

---

## Critical LiveKit behavior (do not regress)

- **`@server.rtc_session()` must not set `agent_name`** unless every join token includes **explicit dispatch** (`RoomAgentDispatch` or Agent Dispatch API). Omitting `agent_name` → **auto-dispatch** when participant joins (avoids “silent room”).
- See comment in `agent.py` near `@server.rtc_session()`.

---

## Configuration (`config.py` / `.env`)

**`.env` is loaded from `research/.env` via path next to `config.py` / `agent.py`** — not only `find_dotenv()` CWD — so subprocess / odd cwd still sees keys.

| Variable | Role |
|----------|------|
| `LIVEKIT_*` | Cloud / self-hosted connection + token minting |
| `SARVAM_API_KEY`, `OPENAI_API_KEY` | STT/TTS + LLM |
| `AGENT_TIMEZONE` | Default `Asia/Kolkata`; “today” / `YYYY-MM-DD` in `build_gujarati_system_prompt()` |
| `CLINIC_NAME` | Default **Patel Dental Clinic**; subject/body/branding |
| `RESEND_API_KEY` | Resend API key (`re_...`) |
| `BOOKING_EMAIL_FROM` | Must be Resend-allowed (e.g. `Patel Dental Clinic <onboarding@resend.dev>` for tests) |
| `BOOKING_EMAIL_TO` | Clinic / hardcoded inbox: merged into the first send (deduped). If **that send fails**, we **retry once** with **only** this address (footer explains the first error). If there are **no patient emails**, we send **only** here when set. |
| `LOG_LEVEL` | `structlog`; `INFO` → JSON logs; `DEBUG` → console renderer |
| `LOG_BOOKING_EMAIL_ADDRESSES` | Default **`true`** — full recipient emails in booking logs. Set `false` to mask. |
| `REENGAGE_*`, `DISABLE_INTERRUPTIONS` | Session behaviour |

Removed from an older design: `N8N_*`, transcript webhooks, hold audio URLs (see older changelog bullets).

---

## Booking email (Resend)

- **Module:** `booking_email.py` — `POST https://api.resend.com/emails` via **stdlib `urllib`** (no extra HTTP dep).
- **Must send a `User-Agent` header** — Resend/Cloudflare returns **403 error 1010** without it (Python `urllib` omits by default). **Fixed in code.**
- Payload includes **`text` + `html`** (Gmail deliverability / rendering).
- **Recipients:** `recipient_emails` from the tool (JSON array string) merged with optional `BOOKING_EMAIL_TO`. **Fallback:** if the primary Resend send fails, **one retry** goes **only** to `BOOKING_EMAIL_TO` (when set and the first attempt was not already that single address), with a footer noting the first error. If there are **no patient emails** but `BOOKING_EMAIL_TO` is set, confirmation is sent **only** there.
- **Flow:** `book_appointment` **awaits** `send_booking_confirmation_email` so room teardown does not cancel the send fire-and-forget.
- **Tool gate:** `email_address_user_confirmed: bool` — if `false`, tool returns `email_not_confirmed` error: **no stub booking, no email**; model must read back email(s), allow corrections, then call again with `true` and final `recipient_emails`.
- Other tool args: `reason_for_visit`, `recipient_emails` JSON array string, etc. (see `tools.py` + `docs/03-tools.md`).

### Implementation details (`booking_email.py`, `tools.py`)

- **`booking_email_configured()`** — `True` only when **both** `RESEND_API_KEY` and `BOOKING_EMAIL_FROM` are non-empty. **`BOOKING_EMAIL_TO` is optional**; without it, a failed primary send has **no** retry recipient.
- **HTTP:** `POST https://api.resend.com/emails` with JSON `from`, `to`, `subject`, `text`, `html`. Headers: **`Authorization: Bearer …`**, **`Content-Type: application/json`**, and a non-empty **`User-Agent`**. Python `urllib` omits `User-Agent` by default → Cloudflare **403 error 1010** if not set (**fixed in code**).
- **Body builders:** `_build_booking_email_body(booking, footer_note=None)` → ASCII subject (`{CLINIC_NAME} - appointment confirmed ({appointment_id})`), plain text + HTML (wrapped in `<pre>` for simple rendering). Fallback retry passes a **`footer_note`** describing the first failure and who was tried.
- **`_send_resend_once`** — one Resend POST inside **`asyncio.to_thread`**; logs `booking_email_resend_post` with **`attempt`** (`primary`, `fallback_hardcoded_to`, …). Returns `sent` + error fields + internal **`attempt`** key (stripped in the public wrapper).
- **`_resolve_recipient_list`** — merges `booking["recipient_emails"]` with **`BOOKING_EMAIL_TO`**, case-insensitive dedupe.
- **Send algorithm (`send_booking_confirmation_email`):**
  1. **Primary** — `to` = merged list above. If merge yields **no** addresses but **`BOOKING_EMAIL_TO`** is set, primary is **`[BOOKING_EMAIL_TO]`** only (log: `booking_email_primary_is_hardcoded_only`). Voice path normally always has ≥1 patient email from the tool; this branch matters for edge/API callers or future changes.
  2. **Fallback** — If primary returns **`sent: false`**, **`BOOKING_EMAIL_TO`** is set, and primary was **not** already exactly that one address → second POST to **`[BOOKING_EMAIL_TO]`** only, with footer note (log: **`booking_email_retry_fallback`**).
- **Return dict** (inspect in **`tool_book_appointment_email_done`** as `resend_detail` on failure, or by adding debug logs): **`delivery_route`** ∈ `primary` \| `fallback_after_primary_failed` \| `primary_failed_no_fallback` \| `primary_failed_already_only_fallback` \| `fallback_failed`; on fallback attempts, **`primary_attempt_failed`** holds the first attempt’s error fields (**no** `attempt` key).
- **`book_appointment` (tool):** Blocks with user-facing `error` unless: **`reason_for_visit`** ≥ 2 chars (trimmed), **`recipient_emails`** parses as JSON array of 1–**10** strings containing `@` (`parse_recipient_emails_json`), **`email_address_user_confirmed`** is `true`. Then stub ledger + **await** `send_booking_confirmation_email(dict(result))`. **Model-visible** fields: `confirmation_email` = `sent` \| `failed` \| `skipped_not_configured`, `resend_id` if sent, `email_error` if failed — **not** `delivery_route` (use structured logs).
- **Resend / patient inboxes:** Test senders (e.g. **`onboarding@resend.dev`**) often allow **only verified recipient domains or addresses** in the Resend project. If the **patient** address is rejected, primary may fail; keep **`BOOKING_EMAIL_TO`** pointed at a **verified** inbox so the **fallback** delivery still works for the demo.

### Structured log events (debug “email didn’t send”)

Search agent stdout (JSON lines) for:

- `booking_email_begin`, `booking_email_skip_not_configured`, `booking_email_no_recipients`, `booking_email_primary_is_hardcoded_only`, `booking_email_resend_post`, `booking_email_http_error`, `booking_email_failed`, `booking_email_sent`, `booking_email_retry_fallback`
- `tool_book_appointment_blocked` (`step`: `reason_for_visit` | `recipient_emails_parse` | `email_not_confirmed`)
- `tool_book_appointment_stub_created`, `tool_book_appointment_email_done` (`outcome`: `sent` | `failed` | `skipped_not_configured`)

**Note:** Many Resend API keys allow **send** but return **401** on `GET /emails/{id}`; use **Resend dashboard** for delivery / `last_event`.

---

## Voice / prompts (`agent.py`)

- **Gujarati-only** assistant rules + **Patel Dental** context.
- **Booking intent:** after user asks to book, ask **reason for visit** early (unless already given), then date/slots.
- **Availability:** `check_availability` returns **exactly three** mock slots (`09:00`, `11:00`, `14:30`); prompt says offer **only** those — no invented times.
- **TTS clock times:** one natural phrase; **never** repeat `વાગ્યા` twice (e.g. avoid `ચાર વાગ્યા વાગ્યા`); English rule block documents patterns for HH:MM → spoken Gujarati.
- **Email:** read-back, corrections, explicit agreement before `email_address_user_confirmed=true`.
- **Tagged speech:** `<NoInterrupt>`, `<Mute>` per `tagged_speech.py`.

---

## Tools summary (`tools.py`)

| Tool | Notes |
|------|--------|
| `check_availability` | Mock: **3** slots only |
| `book_appointment` | Mock ledger + optional Resend; requires `reason_for_visit`, `recipient_emails`, `email_address_user_confirmed` |
| `lookup_customer` | Mock CRM (still has sample email in JSON — not auto-used for send) |
| `cancel_appointment`, `simulate_workflow` | Mock |

---

## Observability

- **`observability.py`:** `transcript_closed` with full transcript JSON (no HTTP webhook in this build).
- **Secrets in Cursor:** workspace `.cursorignore` + `research/.cursorignore` list `.env`; **@-mentioning** `.env` still exposes content in chat.

---

## Docs index

| File | Notes |
|------|--------|
| `docs/01-overview.md` … `docs/07-deployment.md` | Stack, audio, tools, config, prompt, deploy |
| `docs/03-tools.md` | Tools incl. booking + Resend + **fallback** behavior |
| `research/README.md` | Quick start |

---

## For the next agent (switch handoff)

1. **Start here:** this `context.md`, then `agent.py` (prompts + `rtc_session`), `tools.py` (`book_appointment`), `booking_email.py` (Resend).
2. **Env:** `research/.env` — loaded by **path** in `config.py` / `agent.py` so worker cwd does not matter.
3. **Run voice worker:** `cd research && .venv/bin/python agent.py dev` — expect `registered worker` in logs. To replace a stuck process: `pkill -f "/path/to/research/.venv/bin/python agent.py dev"` then start again.
4. **Local browser UI:** `cd research && .venv/bin/python serve_dev_ui.py` → `http://127.0.0.1:8765` (tokens from `.env`).
5. **Regression tests:** `cd research && .venv/bin/python -m pytest tests/test_tools.py -q` (booking + email confirmation gate, mocked send).
6. **Debugging email:** grep logs for `booking_email_*`, `tool_book_appointment_*`; check Resend dashboard for delivery; on failure see `delivery_route` and `booking_email_retry_fallback`.
7. **Out of scope for voice:** `orchestrator.py` LangGraph path — **no** Resend in `execute_action`; parity with `email_address_user_confirmed` not done.

---

## Changelog — earlier session (condensed)

1. `serve_dev_ui.py`, `index.html` token URL fix.
2. Removed `agent_name` from `@server.rtc_session()` → fix silent dispatch.
3. n8n / webhook / hold audio removed; mocks-only tools; docker simplified.
4. Sarvam speaker **`rahul`**; `AGENT_TIMEZONE` + realtime prompt dates.
5. **Not implemented:** auto CRM lookup from SIP/caller ID (`INBOUND_CALLER_PHONE` / participant metadata).

---

## Changelog — 2026-03-26 session (dental demo + Resend + fallback)

1. **Branding:** Patel Dental Clinic; `CLINIC_NAME` in `config.py`; prompts in `agent.py` + clinic copy in stubs/email.
2. **Resend core:** `booking_email.py` — `urllib` POST, **User-Agent** header (fixes **403/1010**), **`text` + `html`** payload, `_send_resend_once`, `_build_booking_email_body` with optional **`footer_note`**.
3. **Primary + fallback delivery:** `send_booking_confirmation_email` — primary `to` = patient **`recipient_emails`** merged with **`BOOKING_EMAIL_TO`** (deduped). On primary failure → **one retry** to **`BOOKING_EMAIL_TO` only** (if set and not redundant), with footer explaining error / attempted recipients. Empty patient list + `BOOKING_EMAIL_TO` set → send only to hardcoded inbox (`booking_email_primary_is_hardcoded_only`). Return metadata: **`delivery_route`**, **`primary_attempt_failed`** when applicable.
4. **Booking tool (`tools.py`):** `reason_for_visit` (required, min length), **`recipient_emails`** as JSON array string (max **10** addresses), **`email_address_user_confirmed`** gate (no book/email until `true` after read-back). **Await** email send after stub create so job teardown does not drop Resend.
5. **`check_availability` stub:** exactly **three** slots (`09:00`, `11:00`, `14:30`); prompts say offer only those.
6. **TTS / prompts:** Gujarati rules so clock times do not double **`વાગ્યા`**; email read-back and confirmation flow documented in tool docstring.
7. **Logging:** `booking_email_*` (incl. `booking_email_retry_fallback`, `booking_email_primary_is_hardcoded_only`) + **`tool_book_appointment_*`**; **`LOG_BOOKING_EMAIL_ADDRESSES`** default **`true`** (mask with `false`).
8. **Docs / repo hygiene:** `context.md` (this file), `docs/03-tools.md`, `.env.example` updated; `.cursorignore` at repo + `research/` for `.env` indexing.
9. **Tests:** `tests/test_tools.py` — `book_appointment` confirmation gate + mocked `send_booking_confirmation_email`.
10. **Orchestrator:** LangGraph `execute_action` still **no** Resend; **not** updated for `email_address_user_confirmed` (voice path only).

---

## Suggested next steps (prioritized)

**Product / demo**

1. **Orchestrator parity:** If LangGraph is used again, add `email_address_user_confirmed` (or equivalent) to `REQUIRED_FIELDS` / `confirm_action` / tests — avoid truthiness bugs on `"yes"`/`"no"` fields.
2. **Auto lookup on answer:** Optional `INBOUND_CALLER_PHONE` or LiveKit participant identity → `lookup_customer` + inject context before first reply (still open from older backlog).
3. **Real calendar:** Replace `stub_check_availability` / booking with HTTP; keep same JSON shapes or update tool docstrings.
4. **Production email:** Verify domain in Resend; stop using `onboarding@resend.dev` for real patients.
5. **Post-process TTS:** Optional code pass to collapse accidental `વાગ્યા વાગ્યા` if the model still slips.

**Hardening**

6. **Rotate secrets** if `.env` or API keys ever appeared in chat logs.
7. **`GET /emails/{id}`:** Use dashboard or full-access API key if automated delivery checks are needed.

---

## Quick file map

```
research/
  agent.py           # GujaratiVoiceAgent, prompts, rtc_session, tts/tag handling
  tools.py           # stubs + ALL_TOOLS + book_appointment (await Resend)
  booking_email.py   # Resend HTTPS (urllib + User-Agent), primary + BOOKING_EMAIL_TO fallback
  config.py          # env path load, CLINIC_NAME, Resend, LOG_BOOKING_EMAIL_ADDRESSES
  session.py         # GujaratiAgentSession, transcript_closed
  observability.py   # structlog
  orchestrator.py    # LangGraph (stubs; no Resend in execute_action)
  tagged_speech.py   # NoInterrupt / Mute
  serve_dev_ui.py    # Local UI :8765
  frontend/index.html
  tests/test_tools.py # book_appointment + email confirmation (mocked send)
  context.md         # this file
```

Workspace root may also contain `.cursorignore` (secrets / indexing).

---

*When extending this doc: append dated bullets under **Changelog**, refresh **Suggested next steps**, and bump **Last updated**.*
