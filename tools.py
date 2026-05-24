"""
Mock function tools for the Gujarati Voice AI Agent.

Used to exercise GPT-4o tool calling and the voice pipeline without any HTTP
backend. Replace stubs with real integrations when you are ready.
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import RunContext, function_tool

from booking_email import send_booking_confirmation_email
from config import CLINIC_NAME, SIP_ENABLED, SIP_OUTBOUND_TRUNK_ID, booking_email_configured
from observability import logger
from sip_helpers import mute_sip_participant, resolve_transfer_target, transfer_cold, transfer_warm_dial

MAX_BOOKING_EMAIL_RECIPIENTS = 10


# ---------------------------------------------------------------------------
# Stub implementations (deterministic test data)
# ---------------------------------------------------------------------------


def parse_recipient_emails_json(recipient_emails: str) -> tuple[list[str] | None, str | None]:
    """
    Parse tool argument `recipient_emails`: a JSON array of strings, e.g. '["a@b.com","c@d.com"]'.
    Returns (addresses, None) on success, or (None, error_message).
    """
    raw = (recipient_emails or "").strip()
    if not raw:
        return None, (
            "recipient_emails is required: pass a JSON array of one or more emails, "
            'e.g. ["patient@example.com"]'
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "recipient_emails must be valid JSON array of strings, e.g. [\"you@example.com\"]"
    if not isinstance(data, list):
        return None, "recipient_emails must be a JSON array of email strings"
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and "@" in item.strip():
            out.append(item.strip())
    if not out:
        return None, "At least one valid email address is required in recipient_emails"
    if len(out) > MAX_BOOKING_EMAIL_RECIPIENTS:
        return None, f"At most {MAX_BOOKING_EMAIL_RECIPIENTS} email addresses are allowed"
    return out, None


def stub_lookup_customer(phone_or_name: str) -> dict[str, Any]:
    return {
        "found": True,
        "customer": {
            "name": "Rajesh Patel",
            "phone": "9876543210",
            "email": "rajesh@example.com",
            "past_appointments": 3,
            "notes": f"{CLINIC_NAME} patient — prefers morning slots. Allergic to penicillin.",
        },
    }


def stub_book_appointment(
    name: str,
    date: str,
    time: str,
    service: str,
    *,
    reason_for_visit: str = "",
    recipient_emails: list[str] | None = None,
) -> dict[str, Any]:
    appointment_id = f"APT-{date.replace('-', '')}-{time.replace(':', '')}"
    return {
        "status": "confirmed",
        "appointment_id": appointment_id,
        "clinic_name": CLINIC_NAME,
        "name": name,
        "date": date,
        "time": time,
        "service": service,
        "reason_for_visit": (reason_for_visit or "").strip(),
        "recipient_emails": list(recipient_emails or []),
    }


def stub_check_availability(date: str) -> dict[str, Any]:
    # Three slots only — easier for voice (patient picks one; extend stub when wiring a real calendar).
    return {
        "date": date,
        "available_slots": ["09:00", "11:00", "14:30"],
    }


def stub_cancel_appointment(appointment_id: str) -> dict[str, Any]:
    return {
        "status": "cancelled",
        "appointment_id": appointment_id,
        "message": "Appointment has been cancelled successfully.",
    }


def stub_simulate_workflow(workflow_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "simulated",
        "workflow_name": workflow_name,
        "received": payload,
        "message": "Mock backend acknowledged the request.",
    }


# ---------------------------------------------------------------------------
# Function tools
# ---------------------------------------------------------------------------


@function_tool()
async def simulate_workflow(
    context: RunContext,
    workflow_name: str,
    data: str = "{}",
) -> dict[str, Any]:
    """Simulate a background automation (mock).

    Use for requests that would normally hit an external system (CRM, calendar,
    messaging). In this build the result is always simulated.

    Args:
        workflow_name: Logical name for the automation (for logging / mock reply).
        data: JSON string of fields to pass through.
    """
    context.disallow_interruptions()
    try:
        parsed = json.loads(data) if data.strip() else {}
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in data parameter"}
    if not isinstance(parsed, dict):
        return {"error": "data must decode to a JSON object"}
    out = stub_simulate_workflow(workflow_name, parsed)
    logger.info("tool_simulate_workflow", workflow_name=workflow_name, keys=list(parsed.keys()))
    return out


@function_tool()
async def lookup_customer(
    context: RunContext,
    phone_or_name: str,
) -> dict[str, Any]:
    """Look up a customer record by phone number or name (mock CRM)."""
    context.disallow_interruptions()
    logger.info("tool_lookup_customer", phone_or_name=phone_or_name)
    return stub_lookup_customer(phone_or_name)


@function_tool()
async def book_appointment(
    context: RunContext,
    name: str,
    date: str,
    time: str,
    service: str,
    reason_for_visit: str,
    recipient_emails: str,
    email_address_user_confirmed: bool,
) -> dict[str, Any]:
    """Book an appointment at the clinic (mock ledger + optional Resend email).

    Before calling: collect name, date, time, service, reason for visit, and one or more
    confirmation email addresses from the patient. You MUST read the final email address(es)
    aloud, allow corrections, read back again after any change, and obtain clear agreement
    — only then set email_address_user_confirmed=true when calling this tool.

    Args:
        name: Patient full name.
        date: YYYY-MM-DD.
        time: HH:MM (24h recommended).
        service: e.g. checkup, cleaning, pain / filling.
        reason_for_visit: Short free-text reason (pain, routine cleaning, follow-up, etc.).
        recipient_emails: JSON array of email strings for confirmation message.
        email_address_user_confirmed: True only after verbal read-back and user approval of
            the exact addresses in recipient_emails (after any corrections).
    """
    context.disallow_interruptions()
    reason = (reason_for_visit or "").strip()
    if len(reason) < 2:
        logger.info(
            "tool_book_appointment_blocked",
            step="reason_for_visit",
            reason_len=len(reason),
        )
        return {
            "error": (
                "reason_for_visit is too short or missing — ask in Gujarati why they need "
                "the visit (e.g. દર્દ, સફાઈ, નિયમિત ચેકઅપ) and pass a short summary here."
            )
        }

    addrs, parse_err = parse_recipient_emails_json(recipient_emails)
    if parse_err:
        logger.info(
            "tool_book_appointment_blocked",
            step="recipient_emails_parse",
            parse_error_preview=(parse_err or "")[:200],
            recipient_emails_raw_preview=(recipient_emails or "")[:120],
        )
        return {"error": parse_err}

    if not email_address_user_confirmed:
        logger.info(
            "tool_book_appointment_blocked",
            step="email_not_confirmed",
            recipient_count=len(addrs or []),
            hint="Model must read back email(s) and set email_address_user_confirmed=true after user agrees.",
        )
        return {
            "error": (
                "email_not_confirmed: In Gujarati, read back the exact email(s) from recipient_emails, "
                "letter by letter or clearly by segments for the part before @. If the user corrects "
                "you, update recipient_emails and read back again until they clearly confirm (હા / બરાબર / "
                "સહી માંજૂર). Only then call book_appointment again with email_address_user_confirmed=true."
            )
        }

    logger.info(
        "tool_book_appointment",
        name=name,
        date=date,
        time=time,
        service=service,
        reason_for_visit=reason,
        recipient_count=len(addrs or []),
        email_address_user_confirmed=True,
    )
    result = stub_book_appointment(
        name,
        date,
        time,
        service,
        reason_for_visit=reason,
        recipient_emails=addrs,
    )
    logger.info(
        "tool_book_appointment_stub_created",
        appointment_id=result.get("appointment_id"),
        booking_email_env_configured=booking_email_configured(),
    )
    # Await send so the room/job cannot tear down before Resend completes (demo reliability).
    if booking_email_configured():
        email_out = await send_booking_confirmation_email(dict(result))
        if email_out.get("sent"):
            resend = email_out.get("resend") or {}
            result = {
                **result,
                "confirmation_email": "sent",
                "resend_id": resend.get("id"),
            }
            logger.info(
                "tool_book_appointment_email_done",
                outcome="sent",
                appointment_id=result.get("appointment_id"),
                resend_id=resend.get("id"),
            )
        else:
            err_parts = [
                email_out.get("resend_message"),
                email_out.get("reason"),
                str(email_out.get("status") or ""),
            ]
            result = {
                **result,
                "confirmation_email": "failed",
                "email_error": " | ".join(p for p in err_parts if p),
            }
            logger.warning(
                "tool_book_appointment_email_done",
                outcome="failed",
                appointment_id=result.get("appointment_id"),
                email_error=result.get("email_error"),
                resend_detail=email_out,
            )
    else:
        result = {**result, "confirmation_email": "skipped_not_configured"}
        logger.info(
            "tool_book_appointment_email_done",
            outcome="skipped_not_configured",
            appointment_id=result.get("appointment_id"),
            hint="RESEND_API_KEY and BOOKING_EMAIL_FROM not both set",
        )
    return result


@function_tool()
async def check_availability(
    context: RunContext,
    date: str,
) -> dict[str, Any]:
    """Check available time slots for a date (mock). Returns exactly three HH:MM options for voice."""
    context.disallow_interruptions()
    logger.info("tool_check_availability", date=date)
    return stub_check_availability(date)


@function_tool()
async def cancel_appointment(
    context: RunContext,
    appointment_id: str,
) -> dict[str, Any]:
    """Cancel an appointment by ID (mock)."""
    context.disallow_interruptions()
    logger.info("tool_cancel_appointment", appointment_id=appointment_id)
    return stub_cancel_appointment(appointment_id)


# ---------------------------------------------------------------------------
# SIP transfer tools (only active when SIP_ENABLED=true)
# ---------------------------------------------------------------------------


def _find_sip_participant(context: RunContext):
    """Find the SIP participant in the room, or None for browser calls."""
    from livekit import rtc

    room = getattr(context.session, "room", None)
    if room is None:
        return None
    for p in room.remote_participants.values():
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return p
    return None


def _get_sip_audio_track_sid(participant) -> str | None:
    """Get the first published audio track SID from a participant."""
    for pub in participant.track_publications.values():
        if pub.kind.name == "KIND_AUDIO":
            return pub.sid
    return None


@function_tool()
async def transfer_call_cold(
    context: RunContext,
    target: str,
) -> dict[str, Any]:
    """Transfer the caller directly to another phone number (cold/blind transfer).

    The caller is immediately connected to the target; the AI agent exits.
    Use for: emergency line, front desk, or a specific doctor.

    Args:
        target: One of "emergency", "front_desk", "dr_patel", "dr_shah",
                or a full phone number like "+919876543210".
    """
    context.disallow_interruptions()

    phone = resolve_transfer_target(target)
    if not phone:
        logger.info("tool_transfer_cold_blocked", target=target, reason="unknown_target")
        return {"error": f"Unknown transfer target: {target}"}

    sip_participant = _find_sip_participant(context)
    if not sip_participant:
        logger.info("tool_transfer_cold_blocked", target=target, reason="no_sip_participant")
        return {"error": "Transfer is only available for phone calls, not browser sessions."}

    logger.info(
        "tool_transfer_cold",
        target=target,
        phone=phone,
        participant=sip_participant.identity,
    )

    room_name = context.session.room.name
    result = await transfer_cold(room_name, sip_participant.identity, phone)
    return result


@function_tool()
async def transfer_call_warm(
    context: RunContext,
    target: str,
    briefing: str,
) -> dict[str, Any]:
    """Transfer the caller with a warm handoff (agent briefs the target first).

    Steps: mute caller → dial target → agent briefs target → unmute caller → agent exits.
    Use when the patient wants to speak to a specific doctor.

    Args:
        target: One of "dr_patel", "dr_shah", or a full phone number.
        briefing: What to tell the target about the patient
                  (e.g. "Patient Rajesh, tooth pain since 2 days, wants consultation").
    """
    import asyncio
    import time

    context.disallow_interruptions()

    phone = resolve_transfer_target(target)
    if not phone:
        logger.info("tool_transfer_warm_blocked", target=target, reason="unknown_target")
        return {"error": f"Unknown transfer target: {target}"}

    if not SIP_OUTBOUND_TRUNK_ID:
        logger.info("tool_transfer_warm_blocked", reason="no_outbound_trunk")
        return {"error": "Outbound SIP trunk not configured (SIP_OUTBOUND_TRUNK_ID)."}

    sip_participant = _find_sip_participant(context)
    if not sip_participant:
        logger.info("tool_transfer_warm_blocked", target=target, reason="no_sip_participant")
        return {"error": "Transfer is only available for phone calls, not browser sessions."}

    track_sid = _get_sip_audio_track_sid(sip_participant)
    room_name = context.session.room.name

    logger.info(
        "tool_transfer_warm",
        target=target,
        phone=phone,
        participant=sip_participant.identity,
        briefing_len=len(briefing),
    )

    # Step 1: Mute the caller so they don't hear the briefing
    if track_sid:
        try:
            await mute_sip_participant(room_name, sip_participant.identity, track_sid, muted=True)
        except Exception as exc:
            logger.warning("tool_transfer_warm_mute_failed", error=str(exc))

    # Step 2: Dial the target into the same room
    doctor_identity = f"sip-doctor-{int(time.time())}"
    try:
        dial_result = await asyncio.wait_for(
            transfer_warm_dial(
                sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
                call_to=phone,
                room_name=room_name,
                participant_identity=doctor_identity,
                participant_name=target,
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        # Doctor didn't answer — unmute caller and report failure
        if track_sid:
            try:
                await mute_sip_participant(
                    room_name, sip_participant.identity, track_sid, muted=False
                )
            except Exception:
                pass
        logger.warning("tool_transfer_warm_timeout", target=target, phone=phone)
        return {
            "status": "failed",
            "error": "Target did not answer within 30 seconds. Caller has been unmuted.",
        }

    if dial_result.get("status") == "error":
        # Dial failed — unmute caller
        if track_sid:
            try:
                await mute_sip_participant(
                    room_name, sip_participant.identity, track_sid, muted=False
                )
            except Exception:
                pass
        return dial_result

    # Step 3: Return success — agent should now speak the briefing to the room
    # (only the doctor hears since caller is muted), then unmute the caller.
    return {
        "status": "warm_transfer_connected",
        "doctor_identity": doctor_identity,
        "caller_identity": sip_participant.identity,
        "caller_track_sid": track_sid,
        "briefing": briefing,
        "instruction": (
            "Doctor is now in the room. Speak the briefing aloud (doctor can hear, caller is muted). "
            "After briefing, the caller will be unmuted automatically. Then say goodbye and exit."
        ),
    }


ALL_TOOLS = [
    simulate_workflow,
    lookup_customer,
    book_appointment,
    check_availability,
    cancel_appointment,
]

if SIP_ENABLED:
    ALL_TOOLS.extend([transfer_call_cold, transfer_call_warm])
