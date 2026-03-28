# Tools (mock backends)

During a voice conversation, GPT-4o can call **function tools**. This project uses **deterministic mocks** only: no HTTP, no n8n. Responses are shaped like real CRM / booking APIs so you can test STT → LLM → tool → TTS end-to-end.

## Flow (simplified)

```
User asks to book / look up / cancel / check slots
        →
GPT-4o calls a tool
        →
Mock function returns JSON immediately
        →
GPT-4o summarizes in Gujarati (spoken via TTS)
```

## Built-in tools (`tools.py`)

| Tool | Purpose | Mock behaviour |
|------|---------|----------------|
| `lookup_customer` | Find customer by phone or name | Always returns sample customer "Rajesh Patel". |
| `book_appointment` | Book with name, date, time, service, **reason_for_visit**, **recipient_emails** (JSON array), **`email_address_user_confirmed`** (must be `true` only after read-back + user approval of those exact addresses) | Returns `confirmed` + …; sends Resend only when confirmed. Primary recipients = patient list merged with optional `BOOKING_EMAIL_TO` in `.env`; **if that send fails**, one **fallback** send goes **only** to `BOOKING_EMAIL_TO` (when set). If `email_address_user_confirmed` is `false`, returns `error` (`email_not_confirmed`) and does not book or email. |
| `check_availability` | Slots for a date | Returns **three** mock times (`09:00`, `11:00`, `14:30`) for short voice prompts. |
| `cancel_appointment` | Cancel by ID | Returns `cancelled` + echo ID. |
| `simulate_workflow` | Generic “automation” | Echoes `workflow_name` and parsed JSON payload. |

Stub helpers (`stub_*`) live in the same file and are reused by `orchestrator.py` for LangGraph demos.

## Adding a real backend later

1. Replace stub implementations in `tools.py` with HTTP calls (or SDKs).
2. Keep the same **JSON shapes** the model already expects, or update tool docstrings so GPT-4o knows the new fields.

## Transcripts

On session close, the full transcript is logged as structured data (`transcript_closed`); there is no webhook export in this build.
