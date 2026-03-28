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
from config import CLINIC_NAME, booking_email_configured
from observability import logger

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


ALL_TOOLS = [
    simulate_workflow,
    lookup_customer,
    book_appointment,
    check_availability,
    cancel_appointment,
]
