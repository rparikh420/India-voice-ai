"""
Gujarati Voice AI Agent

Real-time Gujarati voice assistant, powered by:
  - Sarvam AI STT (saaras:v3) and TTS (bulbul:v3)
  - OpenAI GPT-4o for reasoning and function calling
  - LiveKit for WebRTC audio
  - Mock tools in tools.py (no HTTP backends) for voice-flow testing

Run:
  python agent.py dev        # start the agent server
  python agent.py console    # test in terminal
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterable
from zoneinfo import ZoneInfo

from pathlib import Path

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, FunctionTool, ModelSettings, UserStateChangedEvent, llm
from livekit.agents.voice import SpeechCreatedEvent, SpeechHandle, room_io
from livekit.plugins import noise_cancellation, openai, sarvam, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from config import (
    AGENT_TIMEZONE,
    CLINIC_NAME,
    DISABLE_INTERRUPTIONS,
    MAX_REENGAGE_ATTEMPTS,
    REENGAGE_AFTER_SECONDS,
)
from observability import logger
from session import GujaratiAgentSession
from tagged_speech import enrich_speech_with_tags
from tools import ALL_TOOLS
from tts_coalesce import drain_coalesce_buffer

TTS_COALESCE_MAX_CHARS = 56

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


# ---------------------------------------------------------------------------
# BaseVoiceAgent
# ---------------------------------------------------------------------------


class BaseVoiceAgent(Agent):
    """
    Reusable base class that adds production patterns on top of LiveKit's Agent:

    - Re-engagement when user goes silent (away state)
    - Tagged speech processing in TTS (NoInterrupt, Mute)
    - LLM response capture attached to speech handle via done_callback
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reengage_task: asyncio.Task | None = None
        self._last_speech_handle_created: SpeechHandle | None = None

    async def _reengage(self) -> None:
        """Re-engage the user when they go silent. Waits, then prompts, up to max attempts."""
        for _ in range(MAX_REENGAGE_ATTEMPTS):
            await asyncio.sleep(REENGAGE_AFTER_SECONDS)
            self.session.generate_reply(user_input="[re-engage]")
        if hasattr(self.session, "max_reengage_limit_reached"):
            self.session.max_reengage_limit_reached = True
        self.session.shutdown()

    def _user_state_changed(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "away":
            self._reengage_task = asyncio.create_task(self._reengage())
            return
        if self._reengage_task is not None:
            self._reengage_task.cancel()
            self._reengage_task = None

    def _store_last_speech_handle(self, ev: SpeechCreatedEvent) -> None:
        """Store last speech handle; session.current_speech can be None during preemptive generation."""
        self._last_speech_handle_created = ev.speech_handle

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[FunctionTool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk]:
        """Override to capture full LLM response and attach to speech handle via done_callback."""
        this_speech_handle: SpeechHandle = (
            self.session.current_speech or self._last_speech_handle_created
        )
        llm_full_response = ""

        def set_llm_full_response(sh: SpeechHandle) -> None:
            if len(sh.chat_items) == 1:
                chat_item = sh.chat_items[0]
                chat_item.extra["llm_full_response"] = llm_full_response
            else:
                logger.warning("llm_response_capture", expected=1, got=len(sh.chat_items))

        if this_speech_handle:
            this_speech_handle.add_done_callback(set_llm_full_response)

        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if chunk.delta and chunk.delta.content:
                llm_full_response += chunk.delta.content
            yield chunk

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        """Override to process LLM output through enrich_speech_with_tags."""
        this_speech_handle: SpeechHandle | None = (
            self.session.current_speech or self._last_speech_handle_created
        )

        async def filtered_text_stream(
            input_stream: AsyncIterable[str],
        ) -> AsyncIterable[str]:
            coalesce_buf = ""
            last_interruptible: bool | None = None

            async for speech in enrich_speech_with_tags(input_stream):
                if speech.muted:
                    continue
                if this_speech_handle and not speech.interruptible:
                    this_speech_handle.allow_interruptions = False
                if DISABLE_INTERRUPTIONS and this_speech_handle:
                    this_speech_handle.allow_interruptions = False

                if last_interruptible is not None and speech.interruptible != last_interruptible:
                    parts, coalesce_buf = drain_coalesce_buffer(
                        coalesce_buf, TTS_COALESCE_MAX_CHARS
                    )
                    for p in parts:
                        yield p
                    if coalesce_buf.strip():
                        yield coalesce_buf.strip()
                        coalesce_buf = ""

                last_interruptible = speech.interruptible
                coalesce_buf += speech.text
                parts, coalesce_buf = drain_coalesce_buffer(
                    coalesce_buf, TTS_COALESCE_MAX_CHARS
                )
                for p in parts:
                    yield p

            parts, coalesce_buf = drain_coalesce_buffer(coalesce_buf, TTS_COALESCE_MAX_CHARS)
            for p in parts:
                yield p
            if coalesce_buf.strip():
                yield coalesce_buf.strip()

        async for frame in super().tts_node(filtered_text_stream(text), model_settings):
            yield frame

    async def on_enter(self) -> None:
        self.session.on("user_state_changed", self._user_state_changed)
        self.session.on("speech_created", self._store_last_speech_handle)
        await super().on_enter()

    async def on_exit(self) -> None:
        self.session.off("user_state_changed", self._user_state_changed)
        self.session.off("speech_created", self._store_last_speech_handle)
        if self._reengage_task is not None and not self._reengage_task.done():
            self._reengage_task.cancel()
        await super().on_exit()


# ---------------------------------------------------------------------------
# GujaratiVoiceAgent
# ---------------------------------------------------------------------------

GUJARATI_SYSTEM_PROMPT_BASE = f"""
તમે એક મદદગાર ગુજરાતી વૉઇસ AI સહાયક છો. તમે {CLINIC_NAME} માટે ફોન પર દર્દીઓની મદદ કરો છો: દાંતની સારસંભાળ, નિયમિત ચેકઅપ, ફિલિંગ/દર્દની તપાસ જેવી એપોઇન્ટમેન્ટ, ઉપલબ્ધ સમય જોવો, ગ્રાહક શોધો અને એપોઇન્ટમેન્ટ બુક કરો.

Rules:
- ALWAYS respond in Gujarati (ગુજરાતી). Never switch to English or Hindi unless the user explicitly asks.
- Be warm, polite, and conversational -- speak naturally like a helpful Gujarati-speaking person.
- **Gujarati syntax for speech:** Prefer natural spoken Gujarati word order (verb near the end of the clause, postpositions like માં / ને / પર / થી, not English word order). Use short clauses; avoid translating English one word at a time. If a phrase sounds awkward when read aloud, rephrase it simply before answering.
- **Punctuation for TTS:** Prefer the Gujarati full stop **।** between short spoken sentences (with a space after **।** before the next sentence). Use Latin `.` mainly when needed (e.g. URLs) — avoid extra `.` in the middle of a Gujarati sentence, which confuses pacing.
- Keep responses concise (1-3 sentences). This is a voice conversation, not a text chat.
- When you need to perform an action (book appointment, look up customer, check availability, cancel, etc.), use the available tools.
- Booking intent: જ્યારે વપરાશકર્તા એપોઇન્ટમેન્ટ બુક કરવા કહે, પહેલા નમ્રતાથી સ્વીકારો — પછી ટૂંકમાં પૂછો કે મુલાકાતનું કારણ શું છે (દર્દ, સફાઈ, ચેકઅપ, ફોલો-અપ વગેરે), જ્યાં સુધી તેઓએ પહેલેથી સ્પષ્ટ કારણ ન કહ્યું હોય. કારણ મળ્યા પછી જ તારીખ/સમય/સ્લોટ તરફ આગળ વધો. વાતચીત ગુજરાતીમાં રાખો.
- Typical flow: જરૂર હોય ત્યારે પહેલા ઉપલબ્ધ સમય તપાસો (`check_availability`), પછી વપરાશકર્તા પાસેથી નામ, તારીખ, સમય, સેવા, મુલાકાતનું કારણ (દર્દ / સફાઈ / ચેકઅપ વગેરે), અને કન્ફર્મેશન ઈમેલ લો. **ઈમેલ વચોવચ ચોક્કસ વાંચી સંભળાવો** (@ પહેલાંના ભાગને જરૂર હોય તો અક્ષરદડદી વાંચો). વપરાશકર્તા ભૂલ સુધારે તો નવી ઈમેલ સાથે ફરી વાંચી સુધારો — સ્પષ્ટ હા/બરાબર મળ્યા પછી જ `book_appointment` ચલાવો અને `email_address_user_confirmed=true` આપો. પુષ્ટિ વગર ઈમેલ મોકલાશે નહીં.
- `check_availability` હંમેશાં ચોક્કસ **ત્રણ** સમય સ્લોટ પાછા આપે — વપરાશકર્તાને માત્ર એ જ ત્રણ વિકલ્પો ગુજરાતીમાં ટૂંકમાં કહો; વધારાના કલ્પનાત્મક સમય ન સૂચવો.
- Time-of-day for TTS (clock times): Tool slots are 24-hour strings like 09:00, 11:00, 14:30. When you say them aloud in Gujarati, use **one** natural time phrase only. **Never** say `વાગ્યા` twice in a row — wrong: `ચાર વાગ્યા વાગ્યા`, `સવા ચાર વાગ્યા વાગ્યા`. Prefer a single pattern: e.g. `સવારે નવ વાગ્યા` (09:00), `સવારે અગિયાર વાગ્યા` (11:00), `બપોરે બે વાગ્યાની ત્રીસ મિનિટ` (14:30). Do not combine two different `વાગ્યા` idioms in one slot.
- `book_appointment` માટે `reason_for_visit` માં ટૂંકું વર્ણન લખો (જે દર્દીએ કહ્યું હોય તે). `recipient_emails` માં ચોક્કસ JSON એરે સ્ટ્રિંગ આપો (અંતિમ સંમત સરનામાં). `email_address_user_confirmed` ફક્ત ત્યારે જ `true` જ્યારે વાંચેલી ઈમેલ વપરાશકર્તાએ મોકલા પહેલાં મંજૂર કરી હોય; પહેલી વાર એકઠું કર્યા પછી તરત `true` ન કરો.
- After using a tool, summarize the result clearly to the user in Gujarati.
- જ્યારે `book_appointment` પરિણામમાં `confirmation_email` એ `sent` હોય, કન્ફર્મેશન મોકલાઈ ગયું કહો; જો `failed` હોય તો માફી અને ફરીથી પ્રયાસ/ક્લિનિક પર સંપર્ક કહો; `skipped_not_configured` હોય તો ઈમેલ વિશે વચ્ચે ન બોલો.
- If a user speaks in Gujarati mixed with English words (Gujlish), that's fine -- understand it and respond in Gujarati.
- Do not use any markdown, bullet points, asterisks, or special formatting -- your output goes directly to a TTS engine.
- If you don't understand something, politely ask the user to repeat.

Call transfer (phone calls only — tools available when SIP is enabled):
- જ્યારે દર્દી ગંભીર દર્દ, સોજો, રક્તસ્ત્રાવ, અથવા ઈમરજન્સી જણાવે → દર્દીને જણાવો "હું તમને તાત્કાલિક મદદ સાથે જોડું છું" → `transfer_call_cold` with target="emergency"
- જ્યારે દર્દી ચોક્કસ ડૉક્ટર સાથે વાત કરવા માંગે → warm transfer: "હું ડૉક્ટર ને ફોન કરું છું, એક મિનિટ રાહ જુઓ" → `transfer_call_warm` with target and briefing about the patient context
- ટ્રાન્સફર પહેલાં હંમેશાં દર્દીની મંજૂરી લો। ક્યારેય ચેતવણી વગર ટ્રાન્સફર ન કરો।
- ટ્રાન્સફર target names: "emergency", "front_desk", "dr_patel", "dr_shah"
- If the transfer tool returns an error (e.g. "only available for phone calls"), explain politely in Gujarati that this feature requires a phone call.

Tagged speech:
- When confirming important information like appointment details, wrap your response in <NoInterrupt>...</NoInterrupt> tags so the user cannot accidentally interrupt.
- For internal reasoning, use <Mute>...</Mute> tags.
""".strip()


def build_gujarati_system_prompt() -> str:
    """Append authoritative date/time so relative phrases and tool dates match the real world."""
    try:
        tz = ZoneInfo((AGENT_TIMEZONE or "Asia/Kolkata").strip() or "Asia/Kolkata")
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    # Human-readable + ISO for tools (book_appointment, check_availability expect YYYY-MM-DD)
    local_line = now.strftime("%A, %d %B %Y, %H:%M %Z (UTC%z)")
    iso_date = now.date().isoformat()
    tomorrow_date = (now.date() + timedelta(days=1)).isoformat()

    realtime = f"""
Real-time context (authoritative; use for "today", "tomorrow", "આજે", "આવતી કાલે", and all YYYY-MM-DD tool arguments):
- Time zone: {tz.key}
- Now: {local_line}
- Today (ISO date): {iso_date}
- Tomorrow (ISO date): {tomorrow_date}

Do not guess the year or "current" date from training knowledge — always use the values above.
""".strip()
    return f"{GUJARATI_SYSTEM_PROMPT_BASE}\n\n{realtime}"


class GujaratiVoiceAgent(BaseVoiceAgent):
    """Gujarati-specific voice agent with Sarvam STT/TTS and OpenAI LLM."""

    def __init__(self) -> None:
        super().__init__(
            instructions=build_gujarati_system_prompt(),
            stt=sarvam.STT(
                language="gu-IN",
                model="saaras:v3",
                mode="transcribe",
                flush_signal=True,
            ),
            llm=openai.LLM(model="gpt-4o"),
            tts=sarvam.TTS(
                target_language_code="gu-IN",
                model="bulbul:v3",
                speaker="rahul",
                pace=1.0,
                loudness=1.2,
                enable_preprocessing=True,
            ),
            tools=ALL_TOOLS,
        )

    async def on_enter(self) -> None:
        await super().on_enter()
        self.session.generate_reply(
            instructions=(
                f"Greet the user warmly in Gujarati. Say you assist callers for {CLINIC_NAME} "
                "and ask how you can help (appointment, availability, or other dental enquiry)."
            )
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

server = AgentServer()


# No agent_name here: explicit dispatch would require RoomAgentDispatch on every
# join token or AgentDispatch API calls; without that, the worker never enters
# the room and the user hears silence. Omit agent_name for default auto-dispatch.
@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    logger.info("user_connected", room=ctx.room.name)

    session = GujaratiAgentSession(
        conversation_id=ctx.room.name,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=GujaratiVoiceAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )


# SIP entrypoint: inbound phone calls via Twilio SIP trunk → LiveKit SIP service.
# Dispatch rule (sip/dispatch-rule.json) routes calls to agent_name="inbound-agent".
@server.rtc_session("inbound-agent")
async def sip_entrypoint(ctx: agents.JobContext) -> None:
    """Entrypoint for inbound SIP (phone) calls."""
    # Log caller info from SIP participant attributes
    caller_number = "unknown"
    for p in ctx.room.remote_participants.values():
        caller_number = p.attributes.get("sip.phoneNumber", "unknown")
        break

    logger.info("sip_call_connected", room=ctx.room.name, caller=caller_number)

    session = GujaratiAgentSession(
        conversation_id=ctx.room.name,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=GujaratiVoiceAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
