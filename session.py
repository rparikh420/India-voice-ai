"""
Custom AgentSession for Gujarati voice AI.

Tracks conversation items, builds transcripts on close, and logs them at shutdown.
"""

from __future__ import annotations

import asyncio
import time

from livekit.agents import AgentSession, CloseEvent, ConversationItemAddedEvent

from observability import LatencyMetrics, TranscriptStore, logger


class GujaratiAgentSession(AgentSession):
    """AgentSession subclass with transcript export and lifecycle logging."""

    def __init__(self, conversation_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_id = conversation_id
        self.max_reengage_limit_reached = False
        self._transcript_store = TranscriptStore(conversation_id=conversation_id)
        self._latency_metrics = LatencyMetrics()
        self._started_at: float | None = None

        self.on("close", self._on_close_build_transcript)
        self.on("conversation_item_added", self._on_conversation_item_added)

    def _on_conversation_item_added(self, ev: ConversationItemAddedEvent) -> None:
        """Track user and agent messages as they are added."""
        item = ev.item
        if not hasattr(item, "role") or not hasattr(item, "text_content"):
            return
        role = getattr(item, "role", None)
        text = (getattr(item, "text_content", None) or "").strip()
        if not text:
            return
        transcript_role = "user" if role == "user" else "agent"
        self._transcript_store.add(role=transcript_role, text=text)

    def _on_close_build_transcript(self, ev: CloseEvent) -> None:
        """Build transcript from history and log at close."""
        asyncio.create_task(self._close_handler(ev))

    async def _close_handler(self, ev: CloseEvent) -> None:
        """Async handler for session close: rebuild transcript, export, log metrics."""
        try:
            # Rebuild transcript from history as source of truth
            self._transcript_store.entries.clear()
            for msg in self.history.messages():
                if msg.role not in ("user", "assistant"):
                    continue
                text = (msg.text_content or "").strip()
                if not text:
                    continue
                role = "user" if msg.role == "user" else "agent"
                self._transcript_store.add(role=role, text=text)

            await self._transcript_store.export()

            # Log metrics summary
            metrics_summary = self._latency_metrics.summary()
            duration_sec = (time.time() - self._started_at) if self._started_at else 0
            logger.info(
                "session_closed",
                conversation_id=self.conversation_id,
                reason=str(ev.reason),
                max_reengage_limit_reached=self.max_reengage_limit_reached,
                duration_seconds=round(duration_sec, 1),
                transcript_entries=len(self._transcript_store.entries),
                **metrics_summary,
            )
        except Exception as exc:
            logger.error("session_close_handler_failed", error=str(exc), conversation_id=self.conversation_id)

    async def start(self, *args, **kwargs):
        """Override to log session start."""
        self._started_at = time.time()
        logger.info("session_started", conversation_id=self.conversation_id)
        result = await super().start(*args, **kwargs)
        return result
