"""Tests for mock tools module."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools import (
    ALL_TOOLS,
    stub_book_appointment,
    stub_cancel_appointment,
    stub_check_availability,
    stub_lookup_customer,
    stub_simulate_workflow,
)


def test_stub_lookup_customer_shape():
    r = stub_lookup_customer("9876543210")
    assert r["found"] is True
    assert "customer" in r
    assert r["customer"]["name"] == "Rajesh Patel"


def test_stub_book_appointment_shape():
    r = stub_book_appointment(
        "A",
        "2026-02-21",
        "10:00",
        "checkup",
        reason_for_visit="routine cleaning",
        recipient_emails=["you@example.com", "spouse@example.com"],
    )
    assert r["status"] == "confirmed"
    assert "APT-" in r["appointment_id"]
    assert r["reason_for_visit"] == "routine cleaning"
    assert r["recipient_emails"] == ["you@example.com", "spouse@example.com"]


def test_parse_recipient_emails_json_ok():
    from tools import parse_recipient_emails_json

    addrs, err = parse_recipient_emails_json('["x@y.com"," z@w.com  "]')
    assert err is None
    assert addrs == ["x@y.com", "z@w.com"]


def test_parse_recipient_emails_json_rejects():
    from tools import parse_recipient_emails_json

    assert parse_recipient_emails_json("")[0] is None
    assert parse_recipient_emails_json("not json")[0] is None
    assert parse_recipient_emails_json("[]")[0] is None


def test_stub_check_availability_has_three_slots():
    r = stub_check_availability("2026-02-21")
    assert r["date"] == "2026-02-21"
    assert r["available_slots"] == ["09:00", "11:00", "14:30"]


def test_stub_cancel_appointment_shape():
    r = stub_cancel_appointment("APT-1")
    assert r["status"] == "cancelled"


def test_stub_simulate_workflow_shape():
    r = stub_simulate_workflow("my-flow", {"a": 1})
    assert r["status"] == "simulated"
    assert r["workflow_name"] == "my-flow"
    assert r["received"] == {"a": 1}


def test_all_tools_count():
    assert len(ALL_TOOLS) == 5


@pytest.mark.asyncio
async def test_simulate_workflow_tool_invalid_json():
    from unittest.mock import MagicMock

    from tools import simulate_workflow

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    out = await simulate_workflow(ctx, "w", "not json")
    assert "error" in out


@pytest.mark.asyncio
async def test_simulate_workflow_tool_ok():
    from unittest.mock import MagicMock

    from tools import simulate_workflow

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    out = await simulate_workflow(ctx, "w", json.dumps({"x": 2}))
    assert out["status"] == "simulated"
    assert out["received"] == {"x": 2}


@pytest.mark.asyncio
async def test_book_appointment_blocks_when_email_not_confirmed():
    from unittest.mock import MagicMock

    from tools import book_appointment

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    out = await book_appointment(
        ctx,
        "A",
        "2026-03-26",
        "10:00",
        "cleaning",
        "checkup",
        '["you@example.com"]',
        False,
    )
    assert "error" in out
    assert "email_not_confirmed" in out["error"]


@pytest.mark.asyncio
async def test_book_appointment_ok_when_email_confirmed():
    from unittest.mock import AsyncMock, MagicMock, patch

    from tools import book_appointment

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    with patch("tools.booking_email_configured", return_value=True):
        with patch("tools.send_booking_confirmation_email", new_callable=AsyncMock) as send:
            send.return_value = {"sent": True, "resend": {"id": "email-id-1"}}
            out = await book_appointment(
                ctx,
                "A",
                "2026-03-26",
                "10:00",
                "cleaning",
                "checkup",
                '["you@example.com"]',
                True,
            )
    assert out.get("status") == "confirmed"
    assert out.get("confirmation_email") == "sent"
    send.assert_awaited_once()
