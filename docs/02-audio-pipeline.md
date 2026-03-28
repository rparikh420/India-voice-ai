# Audio Pipeline — How a Conversation Works

This document traces exactly what happens from the moment a user starts speaking to when they hear the agent's response.

## Full pipeline diagram

```
User's microphone
       │
       ▼
┌─────────────────────────────────────────────────────┐
│  LiveKit WebRTC Room                                │
│  (encrypted real-time audio transport)              │
└─────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────┐
│  BVC Noise Cancel   │  Removes keyboard noise, background chatter,
│  (LiveKit plugin)   │  AC hum, etc. before anything processes audio
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Silero VAD         │  Neural network: "Is someone actively talking
│  (voice activity    │  right now, or is this just silence/noise?"
│   detection)        │  Feeds audio frames only when speech is detected
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Sarvam STT         │  Converts Gujarati audio frames → Gujarati text
│  saaras:v3          │  Model: saaras:v3, language: gu-IN
│  mode: transcribe   │  flush_signal=True: sends partial transcripts
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Multilingual Turn  │  The critical decision: "Has the user actually
│  Detector           │  finished their thought, or are they just pausing?"
│  (LiveKit model)    │  Uses context, not just silence duration.
└─────────────────────┘
       │
   user done?
   ┌───┴───┐
  NO      YES
   │       │
   │       ▼
   │  Commit the user's turn → send to LLM
   │
   └── keep buffering audio
```

## Turn-taking in detail

The old approach used `turn_detection="stt"` with a 70ms silence timeout. This caused the agent to interrupt users mid-sentence whenever they paused to think.

The new approach uses two layers:

**Layer 1 — Silero VAD** detects at the frame level whether audio contains speech energy. Only frames with actual speech are sent forward.

**Layer 2 — MultilingualModel turn detector** watches the transcript as it builds and uses a trained model to predict: "given what was said so far and the silence, is this turn complete?" It understands that "I want to book an appointment for..." is incomplete even with a 500ms pause.

The result is that the agent waits for the user to genuinely finish before responding, which feels natural in conversation.

## LLM processing

Once the turn is committed, the transcript goes to GPT-4o with:
- The full conversation history
- The system prompt from `build_gujarati_system_prompt()` in `agent.py` (includes real-time date/time)
- The list of available tools

GPT-4o either generates a text response, or decides to call one or more tools.

**LLM response capture**: The `llm_node()` override in `BaseVoiceAgent` accumulates every token chunk as it streams, then attaches the complete response text to the speech handle's metadata via a `done_callback`. This means you always have the raw LLM output stored, even after TTS transforms it.

## Tagged speech processing

Before the LLM's text reaches Sarvam TTS, it passes through `enrich_speech_with_tags()` in `tagged_speech.py`.

The LLM can embed XML tags in its response to control how it's spoken:

```
"Sure! <NoInterrupt>Your appointment is confirmed for February 25th 
at 10am.</NoInterrupt> Is there anything else I can help you with?"
```

This gets split into three `TaggedSpeech` objects:

| Text | interruptible | muted |
|------|:---:|:---:|
| `Sure! ` | ✓ True | False |
| `Your appointment is confirmed...` | **✗ False** | False |
| ` Is there anything else...` | ✓ True | False |

The `tts_node()` override in `BaseVoiceAgent`:
- **Drops** chunks where `muted=True` (never sent to TTS)
- **Locks** `speech_handle.allow_interruptions = False` for `interruptible=False` chunks — the user physically cannot interrupt that sentence

## TTS output

The filtered text stream goes to Sarvam TTS `bulbul:v3`:
- Language: `gu-IN`
- Speaker: `rahul`
- Pace: `1.0` (natural speed)
- Loudness: `1.2` (slightly boosted)

The resulting audio frames stream back through LiveKit to the user's speaker.

## Re-engagement

When the user goes silent (LiveKit fires `user_state_changed` with `new_state="away"`), the `BaseVoiceAgent` starts an asyncio re-engagement task:

```
User goes away
    │
    ▼
Wait 12 seconds (REENGAGE_AFTER_SECONDS)
    │
    ▼
generate_reply(user_input="[re-engage]")
→ Agent prompts: "Are you still there?"
    │
    ├── User responds → cancel task, resume
    │
    └── Still silent → repeat up to 3 times (MAX_REENGAGE_ATTEMPTS)
                            │
                            ▼
                       session.shutdown()
```

If the user never responds, the session closes cleanly and the transcript is saved.
