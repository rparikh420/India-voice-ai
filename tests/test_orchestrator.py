"""Tests for orchestrator module - pure functions (no LLM calls)."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from orchestrator import (
    REQUIRED_FIELDS,
    ConversationState,
    ask_for_slot,
    check_slots,
    confirm_action,
    respond,
)


# ---------------------------------------------------------------------------
# REQUIRED_FIELDS
# ---------------------------------------------------------------------------


def test_required_fields_has_book_appointment():
    """REQUIRED_FIELDS has entries for book_appointment."""
    assert "book_appointment" in REQUIRED_FIELDS
    assert REQUIRED_FIELDS["book_appointment"] == [
        "name",
        "date",
        "time",
        "service",
        "reason_for_visit",
        "recipient_emails",
    ]


def test_required_fields_has_cancel():
    """REQUIRED_FIELDS has entries for cancel."""
    assert "cancel" in REQUIRED_FIELDS
    assert REQUIRED_FIELDS["cancel"] == ["appointment_id"]


def test_required_fields_has_check_status():
    """REQUIRED_FIELDS has entries for check_status."""
    assert "check_status" in REQUIRED_FIELDS
    assert REQUIRED_FIELDS["check_status"] == ["phone_or_name"]


def test_required_fields_has_check_availability():
    """REQUIRED_FIELDS has entries for check_availability."""
    assert "check_availability" in REQUIRED_FIELDS
    assert REQUIRED_FIELDS["check_availability"] == ["date"]


# ---------------------------------------------------------------------------
# check_slots
# ---------------------------------------------------------------------------


def test_check_slots_all_fields_filled_next_action_confirm():
    """check_slots with all fields filled -> next_action='confirm_action'."""
    state: ConversationState = {
        "user_intent": "book_appointment",
        "collected_fields": {
            "name": "Rajesh Patel",
            "date": "2026-02-21",
            "time": "10:00",
            "service": "dental checkup",
            "reason_for_visit": "routine checkup",
            "recipient_emails": "rajesh@example.com",
        },
    }
    result = check_slots(state)
    assert result["next_action"] == "confirm_action"
    assert result["collected_fields"] == state["collected_fields"]


def test_check_slots_missing_fields_next_action_ask_for_slot():
    """check_slots with missing fields -> next_action='ask_for_slot'."""
    state: ConversationState = {
        "user_intent": "book_appointment",
        "collected_fields": {"name": "Rajesh Patel"},
    }
    result = check_slots(state)
    assert result["next_action"] == "ask_for_slot"
    assert result["collected_fields"] == state["collected_fields"]


# ---------------------------------------------------------------------------
# ask_for_slot
# ---------------------------------------------------------------------------


def test_ask_for_slot_returns_prompt_for_first_missing_field():
    """ask_for_slot returns prompt for the first missing field."""
    state: ConversationState = {
        "user_intent": "book_appointment",
        "collected_fields": {"name": "Rajesh Patel"},
    }
    result = ask_for_slot(state)
    assert result["next_action"] == "respond"
    assert "tool_result" in result
    assert "response" in result["tool_result"]
    # First missing field is "date"; prompt asks "What date would you like?"
    prompt = result["tool_result"]["response"]
    assert "date" in prompt.lower()


# ---------------------------------------------------------------------------
# confirm_action
# ---------------------------------------------------------------------------


def test_confirm_action_includes_collected_field_values():
    """confirm_action includes collected field values in the prompt."""
    state: ConversationState = {
        "user_intent": "book_appointment",
        "collected_fields": {
            "name": "Rajesh Patel",
            "date": "2026-02-21",
            "time": "10:00",
            "service": "dental checkup",
            "reason_for_visit": "routine checkup",
            "recipient_emails": "rajesh@example.com",
        },
    }
    result = confirm_action(state)
    assert result["pending_confirmation"] is True
    assert result["next_action"] == "execute_action"
    assert "tool_result" in result
    prompt = result["tool_result"]["response"]
    assert "Rajesh Patel" in prompt
    assert "2026-02-21" in prompt
    assert "10:00" in prompt
    assert "dental checkup" in prompt
    assert "confirm" in prompt.lower()


# ---------------------------------------------------------------------------
# respond
# ---------------------------------------------------------------------------


def test_respond_returns_tool_result_response_if_present():
    """respond returns tool_result.response if present."""
    state: ConversationState = {
        "tool_result": {"response": "Your appointment is confirmed for 10am."},
    }
    result = respond(state)
    assert "tool_result" in result
    assert result["tool_result"]["response"] == "Your appointment is confirmed for 10am."


def test_respond_returns_error_message_if_tool_result_has_error():
    """respond returns error message if tool_result has error."""
    state: ConversationState = {
        "tool_result": {"error": "Request timed out. Please try again."},
    }
    result = respond(state)
    assert "tool_result" in result
    assert result["tool_result"]["response"] == "Request timed out. Please try again."
