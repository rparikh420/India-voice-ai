# Voice AI Agent — Overview

A production-ready real-time voice AI agent for Indian language businesses. The current deployment speaks and understands **Gujarati**, but the architecture is designed to swap to any Indian language (Hindi, Tamil, Bengali, etc.) with a one-line change.

## What it does

A user calls or connects via browser. The agent:

1. **Listens** — captures microphone audio through LiveKit's WebRTC transport
2. **Understands** — converts Gujarati speech to text using Sarvam AI
3. **Thinks** — reasons with OpenAI GPT-4o, deciding whether to respond or call a tool
4. **Acts** — if a tool is needed (booking, lookup, etc.), runs a **mock** tool and gets JSON back
5. **Speaks** — converts the response back to Gujarati audio using Sarvam AI and plays it to the user

The whole round-trip from "user stops speaking" to "agent starts speaking" takes roughly 1–2 seconds.

## Technology stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Transport | [LiveKit](https://livekit.io) | WebRTC audio streaming |
| Speech-to-Text | [Sarvam AI](https://sarvam.ai) `saaras:v3` | Gujarati speech recognition |
| Language Model | [OpenAI](https://openai.com) `gpt-4o` | Reasoning, conversation, tool calling |
| Text-to-Speech | [Sarvam AI](https://sarvam.ai) `bulbul:v3` | Natural Gujarati speech synthesis |
| Voice Activity | [Silero VAD](https://github.com/snakers4/silero-vad) | Detect when someone is speaking |
| Turn Detection | LiveKit Multilingual Model | Know when user has finished their thought |
| Noise Cancellation | LiveKit BVC | Filter background noise from microphone |
| Tools | In-process mocks | Fake CRM / booking responses for voice testing |
| Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) | Multi-step conversation state machine |
| Observability | [structlog](https://structlog.org) | Structured JSON logging + metrics |

## File map

```
research/
├── agent.py          → Main agent: audio pipeline, turn-taking, re-engagement
├── session.py        → Session lifecycle: transcript, metrics, logging
├── tools.py          → LLM tools: mock stubs + 5 function tools
├── orchestrator.py   → LangGraph: multi-step booking/cancellation flows
├── tagged_speech.py  → XML tag parser: controls interruption mid-speech
├── observability.py  → Logging, latency tracker, transcript store
├── config.py         → All environment variables in one place
├── requirements.txt  → Python dependencies
├── .env.example      → Template for API keys
├── Dockerfile        → Container for the agent
├── docker-compose.yml→ LiveKit + agent
├── livekit.yaml      → LiveKit server config
├── frontend/
│   └── index.html    → Browser test UI (fallback, see main frontend)
├── tests/
│   ├── test_tagged_speech.py
│   ├── test_tools.py
│   └── test_orchestrator.py
└── docs/             ← you are here
```

## Frontend

The production frontend is at `/Users/rparikh/codebases/livekit/frontend/` — a Next.js app cloned from the official LiveKit React starter. It handles token generation automatically and runs at **http://localhost:3000**.

## Running locally

```bash
# Terminal 1: Start the voice agent
cd research
.venv/bin/python agent.py dev

# Terminal 2: Start the frontend
cd ../frontend
npm run dev
# Open http://localhost:3000
```
