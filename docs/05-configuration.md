# Configuration Reference

All configuration is in `config.py`, loaded from `.env`. Copy `.env.example` to `.env` and fill in your values.

## Environment variables

### LiveKit (required)

| Variable | Description | Example |
|----------|-------------|---------|
| `LIVEKIT_URL` | WebSocket URL of your LiveKit project | `wss://myproject.livekit.cloud` |
| `LIVEKIT_API_KEY` | API key from LiveKit Cloud dashboard | `APIxxxxxxxxxxxxx` |
| `LIVEKIT_API_SECRET` | API secret from LiveKit Cloud dashboard | `xxxxxxxxxxxxxxxx` |

Get these from [cloud.livekit.io](https://cloud.livekit.io) → your project → Settings.

### Sarvam AI (required for STT/TTS)

| Variable | Description | Example |
|----------|-------------|---------|
| `SARVAM_API_KEY` | API key from Sarvam dashboard | `sk_xxxxxxxx` |

Get from [dashboard.sarvam.ai](https://dashboard.sarvam.ai).

### OpenAI (required for LLM)

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key from OpenAI | `sk-proj-xxxxxxxx` |

Get from [platform.openai.com/api-keys](https://platform.openai.com/api-keys).

### Agent behaviour

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_TIMEZONE` | `Asia/Kolkata` | IANA timezone for the **Real-time context** block in the system prompt (`today`, tool `YYYY-MM-DD`). Use `UTC`, `America/New_York`, etc. as needed. |
| `REENGAGE_AFTER_SECONDS` | `12` | Seconds of silence before prompting user |
| `MAX_REENGAGE_ATTEMPTS` | `3` | How many times to prompt before ending session |
| `DISABLE_INTERRUPTIONS` | `false` | Set `true` to prevent user from interrupting at any point |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

On session close, the transcript is included in the `transcript_closed` log event (no HTTP export).

## Changing language

To switch from Gujarati to another Indian language, edit two places in `agent.py`:

**1. STT language code:**
```python
stt=sarvam.STT(
    language="hi-IN",  # ← change this
    model="saaras:v3",
    mode="transcribe",
)
```

**2. TTS language code:**
```python
tts=sarvam.TTS(
    target_language_code="hi-IN",  # ← and this
    model="bulbul:v3",
    speaker="ritu",                # ← pick a voice for the language
)
```

**3. System prompt** — update `GUJARATI_SYSTEM_PROMPT_BASE` in `agent.py` (plus any language rules in the runtime date block if needed).

### Supported language codes

| Language | Code |
|----------|------|
| Gujarati | `gu-IN` |
| Hindi | `hi-IN` |
| Bengali | `bn-IN` |
| Tamil | `ta-IN` |
| Telugu | `te-IN` |
| Kannada | `kn-IN` |
| Malayalam | `ml-IN` |
| Marathi | `mr-IN` |
| Punjabi | `pa-IN` |
| Odia | `od-IN` |
| English (Indian) | `en-IN` |

## Changing the voice

In `agent.py`, change `speaker` in `sarvam.TTS(...)`:

**Female voices**: Ritu, Priya, Neha, Pooja, Simran, Kavya, Ishita, Shreya, Roopa, Amelia, Sophia, Tanya, Shruti, Suhani, Kavitha, Rupali

**Male voices**: Shubh, Aditya, Rahul, Rohan, Amit, Dev, Ratan, Varun, Manan, Sumit, Kabir, Aayan, Ashutosh, Advait, Anand, Tarun, Sunny, Mani, Gokul, Vijay, Mohit, Rehan, Soham

## Adjusting speech pace

```python
tts=sarvam.TTS(
    pace=1.0,      # 0.5 = slow, 1.0 = normal, 1.5 = fast
    loudness=1.2,  # 0.5 = quiet, 1.0 = normal, 2.0 = loud
)
```
