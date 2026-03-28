# Extending the Agent

## Adding a new language

The agent architecture is designed for reuse. To support Hindi alongside Gujarati:

**1. Create a new agent class in `agent.py`:**

```python
HINDI_SYSTEM_PROMPT = """
आप एक helpful Hindi voice AI assistant हैं। ...
"""

class HindiVoiceAgent(BaseVoiceAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions=HINDI_SYSTEM_PROMPT,
            stt=sarvam.STT(
                language="hi-IN",
                model="saaras:v3",
                mode="transcribe",
                flush_signal=True,
            ),
            llm=openai.LLM(model="gpt-4o"),
            tts=sarvam.TTS(
                target_language_code="hi-IN",
                model="bulbul:v3",
                speaker="ritu",
                pace=1.0,
                loudness=1.2,
            ),
            tools=ALL_TOOLS,
        )

    async def on_enter(self) -> None:
        await super().on_enter()
        self.session.generate_reply(
            instructions="Greet the user warmly in Hindi."
        )
```

**2. Register a second agent on the server:**

```python
@server.rtc_session(agent_name="hindi-voice-agent")
async def hindi_entrypoint(ctx: agents.JobContext) -> None:
    session = GujaratiAgentSession(
        conversation_id=ctx.room.name,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )
    await session.start(
        agent=HindiVoiceAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )
```

**3. In the frontend**, set `AGENT_NAME=hindi-voice-agent` in `.env.local` to route to the Hindi agent, or build a language selector UI that dispatches to the right agent name.

## Calling the orchestrator from a tool

For complex flows, call the LangGraph orchestrator from within a tool:

```python
from orchestrator import build_orchestrator, run_orchestration

_graph = build_orchestrator()

@function_tool()
async def handle_complex_request(
    context: RunContext,
    user_message: str,
) -> dict[str, Any]:
    """Handle a complex multi-step request using the conversation orchestrator."""
    result = await run_orchestration(
        _graph,
        thread_id=context.session.conversation_id,
        user_message=user_message,
        collected_fields={},
    )
    # result["response"] is text to speak
    # result["state"] has updated slot data
    return {"response": result["response"]}
```

## Adding human handoff

When a user needs a human agent:

**1. Add an escalation tool in `tools.py`:**

```python
from observability import logger

@function_tool()
async def escalate_to_human(
    context: RunContext,
    reason: str,
) -> dict[str, Any]:
    """Transfer this call to a human agent.

    Use when the user explicitly asks for a human, is frustrated,
    or has a request that is beyond your capabilities.

    Args:
        reason: Brief reason for escalation (for the human agent).
    """
    context.disallow_interruptions()

    # Example: POST to your contact-center API, publish a queue event, etc.
    logger.info("escalate_to_human", reason=reason)

    context.session.shutdown()
    return {"status": "escalated"}
```

**2.** Wire `escalate_to_human` to your real escalation path (HTTP, Twilio transfer, LiveKit SIP, etc.).

## Adding persistent conversation memory

To give the agent memory across separate calls (same customer calls back and the agent remembers them):

```python
# In agent.py, after lookup_customer returns customer data,
# inject it into the chat context:

async def on_enter(self) -> None:
    await super().on_enter()
    # If we have a known customer, prime the agent with their history
    if self._customer_data:
        self.session.generate_reply(
            instructions=f"The customer {self._customer_data['name']} has connected. "
                         f"They have had {self._customer_data['past_appointments']} "
                         f"previous appointments. Notes: {self._customer_data['notes']}. "
                         f"Greet them by name in Gujarati."
        )
```

## Adding real-time transcription to the frontend

The React frontend (Next.js app) already receives transcription events from LiveKit. To add a custom transcription view, use the `useTranscriber` hook from `@livekit/components-react`.

## Replacing OpenAI with another LLM

LiveKit agents supports multiple LLM providers. To switch:

```python
# Google Gemini
from livekit.plugins import google
llm=google.LLM(model="gemini-2.5-flash")

# Azure OpenAI
from livekit.plugins import openai as openai_plugin
llm=openai_plugin.LLM(
    model="gpt-4o",
    base_url="https://your-resource.openai.azure.com",
    api_key=os.getenv("AZURE_OPENAI_KEY"),
)

# AWS Bedrock
from livekit.plugins import aws
llm=aws.LLM(model="anthropic.claude-3-5-sonnet-20241022-v2:0")
```

The rest of the pipeline (Sarvam STT/TTS, turn detection, tools) stays the same.
