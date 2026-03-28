"""Optional booking confirmation email via Resend (HTTPS, stdlib only)."""

from __future__ import annotations

import asyncio
import html
import json
import urllib.error
import urllib.request
from typing import Any

from config import (
    BOOKING_EMAIL_FROM,
    BOOKING_EMAIL_TO,
    CLINIC_NAME,
    LOG_BOOKING_EMAIL_ADDRESSES,
    RESEND_API_KEY,
    booking_email_configured,
)
from observability import logger


def _mask_email(addr: str) -> str:
    """Redact email for logs when LOG_BOOKING_EMAIL_ADDRESSES is false."""
    a = (addr or "").strip()
    if "@" not in a:
        return "***"
    local, _, domain = a.partition("@")
    if not local:
        return f"***@{domain}"
    if len(local) == 1:
        masked_local = f"{local}***"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def _recipients_for_log(addresses: list[str]) -> list[str]:
    if LOG_BOOKING_EMAIL_ADDRESSES:
        return list(addresses)
    return [_mask_email(a) for a in addresses]


def _build_booking_email_body(
    booking: dict[str, Any],
    *,
    footer_note: str | None = None,
) -> tuple[str, str, str]:
    """Return (subject, plain text body, html body)."""
    apt_id = booking.get("appointment_id", "")
    name = booking.get("name", "")
    date = booking.get("date", "")
    time = booking.get("time", "")
    service = booking.get("service", "")
    reason = (booking.get("reason_for_visit") or "").strip()
    # ASCII subject avoids occasional issues with encoded headers at providers.
    subject = f"{CLINIC_NAME} - appointment confirmed ({apt_id})"
    reason_block = f"Reason for visit: {reason}\n" if reason else ""
    text = (
        f"Thank you for choosing {CLINIC_NAME}.\n\n"
        f"Your appointment is confirmed.\n\n"
        f"Confirmation: {apt_id}\n"
        f"Patient: {name}\n"
        f"Date: {date}\n"
        f"Time: {time}\n"
        f"Service: {service}\n"
        f"{reason_block}\n"
        f"If you need to change or cancel, please call the clinic.\n"
    )
    if footer_note:
        text = f"{text.rstrip()}\n\n---\n{footer_note.strip()}\n"
    safe = html.escape(text)
    html_body = (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,Segoe UI,sans-serif;"
        "line-height:1.5;color:#111\">"
        f"<h2 style=\"margin:0 0 12px;font-size:18px\">{html.escape(CLINIC_NAME)}</h2>"
        f"<pre style=\"white-space:pre-wrap;font-family:inherit;margin:0\">{safe}</pre>"
        "</body></html>"
    )
    return subject, text, html_body


async def _send_resend_once(
    *,
    to_addresses: list[str],
    subject: str,
    text: str,
    html_body: str,
    appointment_id: str | None,
    attempt_label: str,
) -> dict[str, Any]:
    """One Resend POST; returns same shape as send_booking_confirmation_email (sent + detail keys)."""
    recipients_log = _recipients_for_log(to_addresses)
    logger.info(
        "booking_email_resend_post",
        appointment_id=appointment_id,
        attempt=attempt_label,
        recipient_count=len(to_addresses),
        recipients=recipients_log,
        from_preview=(BOOKING_EMAIL_FROM or "")[:72],
        subject=subject[:120],
    )
    try:
        result = await asyncio.to_thread(
            _send_via_resend,
            to_addresses=to_addresses,
            subject=subject,
            text=text,
            html_body=html_body,
        )
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        resend_message = err_body[:2000]
        try:
            parsed = json.loads(err_body)
            if isinstance(parsed, dict):
                resend_message = str(
                    parsed.get("message") or parsed.get("name") or parsed.get("error") or err_body
                )[:2000]
        except json.JSONDecodeError:
            pass
        logger.warning(
            "booking_email_http_error",
            status=e.code,
            body=err_body[:2000],
            appointment_id=appointment_id,
            attempt=attempt_label,
            recipients=recipients_log,
            resend_message_short=resend_message[:500],
        )
        return {
            "sent": False,
            "reason": "resend_http_error",
            "status": e.code,
            "resend_message": resend_message,
            "attempt": attempt_label,
        }
    except Exception as e:
        logger.warning(
            "booking_email_failed",
            error=str(e),
            exc_type=type(e).__name__,
            appointment_id=appointment_id,
            attempt=attempt_label,
            recipients=recipients_log,
        )
        return {"sent": False, "reason": str(e), "attempt": attempt_label}

    logger.info(
        "booking_email_sent",
        resend_id=result.get("id"),
        appointment_id=appointment_id,
        attempt=attempt_label,
        recipient_count=len(to_addresses),
        recipients=recipients_log,
    )
    return {"sent": True, "resend": result, "attempt": attempt_label}


def _resolve_recipient_list(booking: dict[str, Any]) -> list[str]:
    """Merge patient-supplied emails with optional staff copy; dedupe preserving order."""
    raw = booking.get("recipient_emails") or []
    seen: set[str] = set()
    out: list[str] = []
    for addr in raw if isinstance(raw, list) else []:
        if not isinstance(addr, str):
            continue
        normalized = addr.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    staff = (BOOKING_EMAIL_TO or "").strip()
    if staff:
        key = staff.casefold()
        if key not in seen:
            seen.add(key)
            out.append(staff)
    return out


def _send_via_resend(
    *, to_addresses: list[str], subject: str, text: str, html_body: str
) -> dict[str, Any]:
    """POST to Resend API; raises on HTTP error."""
    payload = json.dumps(
        {
            "from": BOOKING_EMAIL_FROM,
            "to": to_addresses,
            "subject": subject,
            "text": text,
            "html": html_body,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Resend/Cloudflare returns 403 error 1010 without User-Agent (urllib omits it by default).
            "User-Agent": "patel-dental-voice-agent/1.0 (booking-email; +https://resend.com/docs)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


async def send_booking_confirmation_email(booking: dict[str, Any]) -> dict[str, Any]:
    """
    Send one transactional email for a confirmed booking.

    1) Primary attempt: patient `recipient_emails` plus optional `BOOKING_EMAIL_TO` (deduped).
    2) If that fails and `BOOKING_EMAIL_TO` is set, retry once with **only** that address
       (hardcoded clinic inbox) and a short footer explaining the primary failure.
    If there are no patient emails but `BOOKING_EMAIL_TO` is set, sends only to the hardcoded address.
    """
    apt_id = booking.get("appointment_id")
    raw_booking_recipients = booking.get("recipient_emails")

    configured = booking_email_configured()
    has_key = bool((RESEND_API_KEY or "").strip())
    has_from = bool((BOOKING_EMAIL_FROM or "").strip())
    has_staff_to = bool((BOOKING_EMAIL_TO or "").strip())

    logger.info(
        "booking_email_begin",
        appointment_id=apt_id,
        resend_configured=configured,
        has_resend_api_key=has_key,
        has_booking_email_from=has_from,
        has_booking_email_to_env=has_staff_to,
        booking_recipient_count=len(raw_booking_recipients) if isinstance(raw_booking_recipients, list) else 0,
    )

    if not configured:
        logger.warning(
            "booking_email_skip_not_configured",
            appointment_id=apt_id,
            has_resend_api_key=has_key,
            has_booking_email_from=has_from,
            hint="Set RESEND_API_KEY and BOOKING_EMAIL_FROM in research/.env",
        )
        return {"sent": False, "reason": "email_not_configured"}

    fallback_to = (BOOKING_EMAIL_TO or "").strip()
    primary_to = _resolve_recipient_list(booking)

    if not primary_to:
        if fallback_to:
            primary_to = [fallback_to]
            logger.info(
                "booking_email_primary_is_hardcoded_only",
                appointment_id=apt_id,
                reason="no_patient_recipients_in_booking",
            )
        else:
            logger.warning(
                "booking_email_no_recipients",
                appointment_id=apt_id,
                raw_recipient_emails_type=type(raw_booking_recipients).__name__,
            )
            return {"sent": False, "reason": "no_recipients"}

    subject, text, html_body = _build_booking_email_body(booking)
    first = await _send_resend_once(
        to_addresses=primary_to,
        subject=subject,
        text=text,
        html_body=html_body,
        appointment_id=apt_id,
        attempt_label="primary",
    )
    if first.get("sent"):
        out = {k: v for k, v in first.items() if k != "attempt"}
        out["delivery_route"] = "primary"
        return out

    if not fallback_to:
        out = {k: v for k, v in first.items() if k != "attempt"}
        out["delivery_route"] = "primary_failed_no_fallback"
        return out

    primary_casefold = {a.casefold() for a in primary_to}
    if primary_casefold == {fallback_to.casefold()}:
        out = {k: v for k, v in first.items() if k != "attempt"}
        out["delivery_route"] = "primary_failed_already_only_fallback"
        return out

    err_summary = first.get("resend_message") or first.get("reason") or "unknown"
    attempted = ", ".join(_recipients_for_log(primary_to))
    footer = (
        "Clinic fallback: the first booking confirmation send failed. "
        f"First attempt recipients: {attempted}. "
        f"Error summary: {err_summary[:500]}"
    )
    logger.warning(
        "booking_email_retry_fallback",
        appointment_id=apt_id,
        fallback_to=_recipients_for_log([fallback_to])[0],
        primary_error_preview=str(err_summary)[:300],
    )
    subject2, text2, html2 = _build_booking_email_body(booking, footer_note=footer)
    second = await _send_resend_once(
        to_addresses=[fallback_to],
        subject=subject2,
        text=text2,
        html_body=html2,
        appointment_id=apt_id,
        attempt_label="fallback_hardcoded_to",
    )
    if second.get("sent"):
        out = {k: v for k, v in second.items() if k != "attempt"}
        out["delivery_route"] = "fallback_after_primary_failed"
        out["primary_attempt_failed"] = {k: v for k, v in first.items() if k != "attempt"}
        return out
    out = {k: v for k, v in second.items() if k != "attempt"}
    out["delivery_route"] = "fallback_failed"
    out["primary_attempt_failed"] = {k: v for k, v in first.items() if k != "attempt"}
    return out


async def send_booking_confirmation_safe(booking: dict[str, Any]) -> None:
    """Log outcomes only; never raises."""
    out = await send_booking_confirmation_email(booking)
    if not out.get("sent"):
        logger.info(
            "booking_email_skipped_or_failed",
            appointment_id=booking.get("appointment_id"),
            detail=out,
        )
