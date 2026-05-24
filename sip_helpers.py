"""
LiveKit SIP API helpers for call transfer operations.

Wraps the LiveKit server API for cold transfers, warm transfers (outbound dial),
and participant muting. All functions are async and return status dicts.
"""

from __future__ import annotations

import re
from typing import Any

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest, TransferSIPParticipantRequest

from config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    TRANSFER_DOCTOR_NUMBERS,
    TRANSFER_EMERGENCY_NUMBER,
)
from observability import logger

# Matches E.164 phone numbers: +<country><number>, 7-15 digits
_PHONE_RE = re.compile(r"^\+\d{7,15}$")

# Fixed targets beyond doctor numbers
_FIXED_TARGETS: dict[str, str] = {}
if TRANSFER_EMERGENCY_NUMBER:
    _FIXED_TARGETS["emergency"] = TRANSFER_EMERGENCY_NUMBER
    _FIXED_TARGETS["front_desk"] = TRANSFER_EMERGENCY_NUMBER  # alias until separate number configured


def get_livekit_api() -> api.LiveKitAPI:
    """Create a LiveKit API client from environment config."""
    return api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)


def resolve_transfer_target(target: str) -> str | None:
    """Map a symbolic target name or raw phone number to an E.164 number.

    Returns None if the target cannot be resolved.
    """
    target = target.strip()

    # Check fixed targets (emergency, front_desk)
    if target.lower() in _FIXED_TARGETS:
        return _FIXED_TARGETS[target.lower()]

    # Check doctor numbers
    if target.lower() in TRANSFER_DOCTOR_NUMBERS:
        return TRANSFER_DOCTOR_NUMBERS[target.lower()]

    # Raw phone number pass-through
    if _PHONE_RE.match(target):
        return target

    return None


async def transfer_cold(
    room_name: str,
    participant_identity: str,
    transfer_to: str,
) -> dict[str, Any]:
    """Execute a cold (blind) SIP transfer.

    The caller is immediately connected to transfer_to; the agent exits.
    """
    lk = get_livekit_api()
    try:
        request = TransferSIPParticipantRequest(
            room_name=room_name,
            participant_identity=participant_identity,
            transfer_to=transfer_to,
        )
        await lk.sip.transfer_sip_participant(request)
        logger.info(
            "sip_transfer_cold",
            room=room_name,
            participant=participant_identity,
            transfer_to=transfer_to,
        )
        return {"status": "transferred", "transfer_to": transfer_to}
    except Exception as exc:
        logger.error(
            "sip_transfer_cold_failed",
            room=room_name,
            error=str(exc),
        )
        return {"status": "error", "error": str(exc)}
    finally:
        await lk.aclose()


async def transfer_warm_dial(
    sip_trunk_id: str,
    call_to: str,
    room_name: str,
    participant_identity: str,
    participant_name: str,
) -> dict[str, Any]:
    """Dial an outbound SIP call into a room for warm transfer.

    Creates a new SIP participant (the doctor/target) in the same room as the caller.
    """
    lk = get_livekit_api()
    try:
        request = CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=call_to,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_name=participant_name,
            krisp_enabled=True,
        )
        result = await lk.sip.create_sip_participant(request)
        logger.info(
            "sip_warm_dial",
            room=room_name,
            call_to=call_to,
            participant=participant_identity,
            sip_call_id=getattr(result, "sip_call_id", None),
        )
        return {
            "status": "connected",
            "call_to": call_to,
            "participant_identity": participant_identity,
            "sip_call_id": getattr(result, "sip_call_id", None),
        }
    except Exception as exc:
        logger.error(
            "sip_warm_dial_failed",
            room=room_name,
            call_to=call_to,
            error=str(exc),
        )
        return {"status": "error", "error": str(exc)}
    finally:
        await lk.aclose()


async def mute_sip_participant(
    room_name: str,
    identity: str,
    track_sid: str,
    muted: bool,
) -> None:
    """Mute or unmute a participant's audio track in a room."""
    lk = get_livekit_api()
    try:
        await lk.room.mute_published_track(
            api.MuteRoomTrackRequest(
                room=room_name,
                identity=identity,
                track_sid=track_sid,
                muted=muted,
            )
        )
        logger.info(
            "sip_mute_participant",
            room=room_name,
            identity=identity,
            track_sid=track_sid,
            muted=muted,
        )
    except Exception as exc:
        logger.error(
            "sip_mute_failed",
            room=room_name,
            identity=identity,
            error=str(exc),
        )
        raise
    finally:
        await lk.aclose()
