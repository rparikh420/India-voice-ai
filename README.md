# Gujarati Voice AI Agent

A real-time voice AI agent that speaks and understands Gujarati, built for Indian businesses. Uses Sarvam AI (STT/TTS), OpenAI (reasoning + tool calls), LiveKit (WebRTC), and **mock tools** so you can test the voice loop without backends. LangGraph code remains for multi-step flow experiments.

## How It Works

```
User speaks Gujarati
     |
     v
LiveKit (WebRTC) + Noise Cancellation (BVC)
     |
     v
Silero VAD + Multilingual Turn Detector
     |
     v
Sarvam STT (saaras:v3) --> Gujarati text
     |
     v
OpenAI GPT-4o --> intent + reasoning + tool calls (mock CRM / booking)
     |                |
     |       LangGraph orchestrator (optional multi-step flows)
     |
     v
Tagged Speech Processor (<NoInterrupt>, <Mute>)
     |
     v
Sarvam TTS (bulbul:v3) --> Gujarati audio
     |
     v
User hears response
```

## Features

- **Turn-taking**: Multilingual turn detector + Silero VAD (not just STT endpointing)
- **Interruption control**: `<NoInterrupt>` tags prevent accidental interruptions during confirmations
- **Re-engagement**: Auto-prompts silent users, shuts down after max attempts
- **Mock tools**: Lookup, book, availability, cancel, and generic `simulate_workflow` (no HTTP)
- **LangGraph orchestration** (module): Multi-step booking-style flows using the same mocks
- **Observability**: Structured logging, latency metrics, transcript logged on session close
- **Noise cancellation**: LiveKit BVC for clean audio input

## Prerequisites

- Python 3.10+
- API keys for:
  - [LiveKit Cloud](https://cloud.livekit.io) (free tier available)
  - [Sarvam AI](https://dashboard.sarvam.ai)
  - [OpenAI](https://platform.openai.com/api-keys)
- [LiveKit CLI](https://docs.livekit.io/home/cli/cli-setup/) (for generating test tokens)

## Quick Start

### 1. Install

```bash
cd research
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
python agent.py dev
```

### 4. Test in terminal

```bash
python agent.py console
```

### 5. Test in browser

**Simplest** (uses `.env`; no `lk` CLI):

Terminal A: `python agent.py dev`  
Terminal B: `python serve_dev_ui.py`  
Open **http://127.0.0.1:8765** — URL and token load automatically; click **Connect**.

Or open `frontend/index.html` directly and paste a token from:

```bash
lk token create \
  --api-key YOUR_API_KEY \
  --api-secret YOUR_API_SECRET \
  --join --room test --identity user1 \
  --valid-for 24h
```

### 6. Run with Docker

```bash
docker compose up -d
```

This starts LiveKit server and the voice agent.

## Project Structure

```
research/
├── agent.py              # Main agent: BaseVoiceAgent + GujaratiVoiceAgent + entrypoint
├── session.py            # Custom AgentSession + transcript logging on close
├── tools.py              # Mock function tools (stub_* + @function_tool)
├── orchestrator.py       # LangGraph conversation state machine
├── tagged_speech.py      # XML tag parser for NoInterrupt/Mute
├── observability.py      # Structured logging, latency metrics, transcript logging
├── config.py             # Centralized environment configuration
├── requirements.txt      # Python dependencies
├── .env.example          # API key template
├── Dockerfile            # Agent container
├── docker-compose.yml    # LiveKit + agent
├── livekit.yaml          # LiveKit server config for docker-compose
├── frontend/
│   └── index.html        # Browser UI with state indicators
├── tests/
│   ├── test_tagged_speech.py
│   ├── test_tools.py
│   └── test_orchestrator.py
├── context.md            # Handoff for new agents: state + session changelog
├── serve_dev_ui.py        # Local browser UI + token minting
└── README.md
```

## Architecture

| Component | Technology | Role |
|-----------|-----------|------|
| STT | Sarvam `saaras:v3` | Gujarati speech to text |
| LLM | OpenAI `gpt-4o` | Understanding, reasoning, tool calls |
| TTS | Sarvam `bulbul:v3` | Text to Gujarati speech |
| VAD | Silero VAD | Voice activity detection |
| Turn Detection | LiveKit Multilingual Model | Context-aware turn boundaries |
| Transport | LiveKit WebRTC | Real-time audio streaming |
| Noise Cancel | LiveKit BVC | Background noise removal |
| Orchestration | LangGraph | Multi-step flows (optional; same mocks in `orchestrator`) |
| Tools | In-process mocks | Deterministic JSON for voice/tool testing |
| Observability | structlog | Structured JSON logging + metrics |

## Customization

### Change the language

Edit `agent.py` and swap the language code in the STT/TTS config:

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

### Add business tools

Edit `tools.py` — each tool is an async function with `@function_tool()` and a clear docstring (the model uses it to decide when to call). Swap stub bodies for real HTTP/SDK calls when you integrate a backend.

### LangGraph orchestration

The orchestrator (`orchestrator.py`) manages multi-step flows using the same `stub_*` helpers in `execute_action`. To extend:

1. Add required fields to `REQUIRED_FIELDS`
2. Extend `execute_action` with new intent → mock (or real) handler
3. The graph handles slot-filling, confirmation, and execution

## Running Tests

```bash
pytest tests/ -v
```

## Troubleshooting

**"Module not found"** -- Run `pip install -r requirements.txt` again.

**"Invalid API key"** -- Check `.env` has correct keys with no extra spaces.

**Poor transcription** -- Ensure minimal background noise; the BVC noise canceller helps.

**Agent not responding** -- Ensure `python agent.py dev` is running before connecting.

**Tools** — All tool calls are mocks in this build; no extra env vars for backends.

