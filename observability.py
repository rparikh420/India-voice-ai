"""
Observability: structured logging, latency metrics, and transcript logging.

Uses ``structlog`` for JSON-structured logs and lightweight in-process
metric tracking. Session transcripts are logged at close (no HTTP export).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from config import LOG_LEVEL

# ---------------------------------------------------------------------------
# Structured logger setup
# ---------------------------------------------------------------------------

_NAME_TO_LEVEL = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if LOG_LEVEL == "DEBUG" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        _NAME_TO_LEVEL.get(LOG_LEVEL.lower(), 20)
    ),
    cache_logger_on_first_use=True,
)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


logger = get_logger("voice-agent")


# ---------------------------------------------------------------------------
# Latency tracker
# ---------------------------------------------------------------------------


@dataclass
class LatencyMetrics:
    """Accumulates round-trip latency samples for each pipeline stage."""

    stt_samples: list[float] = field(default_factory=list)
    llm_samples: list[float] = field(default_factory=list)
    tts_samples: list[float] = field(default_factory=list)
    total_samples: list[float] = field(default_factory=list)

    def record_stt(self, duration: float) -> None:
        self.stt_samples.append(duration)

    def record_llm(self, duration: float) -> None:
        self.llm_samples.append(duration)

    def record_tts(self, duration: float) -> None:
        self.tts_samples.append(duration)

    def record_total(self, duration: float) -> None:
        self.total_samples.append(duration)

    @staticmethod
    def _avg(samples: list[float]) -> float:
        return sum(samples) / len(samples) if samples else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "stt_avg_ms": round(self._avg(self.stt_samples) * 1000, 1),
            "stt_count": len(self.stt_samples),
            "llm_avg_ms": round(self._avg(self.llm_samples) * 1000, 1),
            "llm_count": len(self.llm_samples),
            "tts_avg_ms": round(self._avg(self.tts_samples) * 1000, 1),
            "tts_count": len(self.tts_samples),
            "total_avg_ms": round(self._avg(self.total_samples) * 1000, 1),
            "total_count": len(self.total_samples),
        }


class Timer:
    """Context-manager stopwatch that records to a LatencyMetrics instance."""

    def __init__(self, metrics: LatencyMetrics, stage: str):
        self._metrics = metrics
        self._stage = stage
        self._start: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        elapsed = time.perf_counter() - self._start
        recorder = getattr(self._metrics, f"record_{self._stage}", None)
        if recorder:
            recorder(elapsed)


# ---------------------------------------------------------------------------
# Transcript export
# ---------------------------------------------------------------------------


@dataclass
class TranscriptEntry:
    role: str  # "user" | "agent"
    text: str
    timestamp: float  # epoch seconds


@dataclass
class TranscriptStore:
    conversation_id: str
    entries: list[TranscriptEntry] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        self.entries.append(TranscriptEntry(role=role, text=text, timestamp=time.time()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "entries": [
                {"role": e.role, "text": e.text, "timestamp": e.timestamp}
                for e in self.entries
            ],
        }

    async def export(self, webhook_url: Optional[str] = None) -> None:
        _ = webhook_url  # reserved for future HTTP export
        logger.info(
            "transcript_closed",
            conversation_id=self.conversation_id,
            entry_count=len(self.entries),
            payload=self.to_dict(),
        )
