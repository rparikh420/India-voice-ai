# Documentation

**Agent handoff / project state:** [context.md](../context.md) — running notes, chat-session changelog, pitfalls (e.g. LiveKit dispatch).

| Doc | What it covers |
|-----|---------------|
| [01 — Overview](01-overview.md) | What this is, full tech stack, file map, how to run |
| [02 — Audio Pipeline](02-audio-pipeline.md) | Step-by-step: microphone → VAD → STT → LLM → TTS → speaker, turn-taking, re-engagement |
| [03 — Tools](03-tools.md) | Mock function tools for voice testing; adding real backends later |
| [04 — LangGraph Orchestrator](04-langgraph-orchestrator.md) | Multi-step conversation state machine, slot filling, confirmation flows |
| [05 — Configuration](05-configuration.md) | All environment variables, language/voice switching reference |
| [06 — System Prompt](06-system-prompt.md) | The GPT-4o prompt, speech tags (NoInterrupt/Mute), customization examples |
| [07 — Deployment](07-deployment.md) | Local dev, Docker Compose, cloud VM, scaling, monitoring |
| [08 — Extending](08-extending.md) | Adding languages, human handoff, persistent memory, swapping LLM providers |
