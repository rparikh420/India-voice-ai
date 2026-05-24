"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Always load research/.env regardless of process cwd (LiveKit workers may not start in research/).
_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)

# ---------------------------------------------------------------------------
# LiveKit
# ---------------------------------------------------------------------------
LIVEKIT_URL: str = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY: str = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET: str = os.getenv("LIVEKIT_API_SECRET", "")

# ---------------------------------------------------------------------------
# Inference providers
# ---------------------------------------------------------------------------
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------
AGENT_TIMEZONE: str = os.getenv("AGENT_TIMEZONE", "Asia/Kolkata")
REENGAGE_AFTER_SECONDS: int = int(os.getenv("REENGAGE_AFTER_SECONDS", "12"))
MAX_REENGAGE_ATTEMPTS: int = int(os.getenv("MAX_REENGAGE_ATTEMPTS", "3"))
DISABLE_INTERRUPTIONS: bool = os.getenv("DISABLE_INTERRUPTIONS", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Patel Dental Clinic — branding (used in prompts, tools, emails)
# ---------------------------------------------------------------------------
CLINIC_NAME: str = os.getenv("CLINIC_NAME", "Patel Dental Clinic")

# ---------------------------------------------------------------------------
# Optional: booking confirmation email via Resend (https://resend.com)
# Send is fire-and-forget from the agent; if unset, booking still succeeds.
# ---------------------------------------------------------------------------
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
BOOKING_EMAIL_FROM: str = os.getenv("BOOKING_EMAIL_FROM", "")
BOOKING_EMAIL_TO: str = os.getenv("BOOKING_EMAIL_TO", "")


def booking_email_configured() -> bool:
    """True when Resend can send: API key + From. Recipient addresses come from the booking tool."""
    return bool(RESEND_API_KEY.strip() and BOOKING_EMAIL_FROM.strip())


# Log full recipient emails in structured logs (default on). Set LOG_BOOKING_EMAIL_ADDRESSES=false to mask.
_LOG_BOOKING_EMAIL_RAW = (os.getenv("LOG_BOOKING_EMAIL_ADDRESSES", "true") or "true").strip().lower()
LOG_BOOKING_EMAIL_ADDRESSES: bool = _LOG_BOOKING_EMAIL_RAW not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# SIP / Telephony (Twilio SIP trunking via LiveKit SIP service)
# ---------------------------------------------------------------------------
SIP_ENABLED: bool = os.getenv("SIP_ENABLED", "false").lower() == "true"
TRANSFER_EMERGENCY_NUMBER: str = os.getenv("TRANSFER_EMERGENCY_NUMBER", "")
SIP_OUTBOUND_TRUNK_ID: str = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")


def _parse_doctor_numbers() -> dict[str, str]:
    """Parse TRANSFER_DOCTOR_NUMBERS JSON env var into a dict of name→phone."""
    import json as _json

    raw = os.getenv("TRANSFER_DOCTOR_NUMBERS", "{}")
    try:
        d = _json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


TRANSFER_DOCTOR_NUMBERS: dict[str, str] = _parse_doctor_numbers()


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
