"""
LangGraph orchestrator for complex multi-step conversation flows.

Used as an ADDITION to the standard LiveKit voice pipeline. Invoked from within
tool execution when stateful workflows are needed (multi-step booking,
confirmation, escalation). Does NOT replace the LiveKit pipeline.

Usage:
    graph = build_orchestrator()
    result = await run_orchestration(
        graph, thread_id="call-123",
        user_message="I want to book an appointment",
        collected_fields={},
    )
    # result = {"response": "Sure! What date works for you?", "state": {...}}
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver

from observability import logger
from tools import (
    stub_book_appointment,
    stub_cancel_appointment,
    stub_check_availability,
    stub_lookup_customer,
)

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, list[str]] = {
    "book_appointment": ["name", "date", "time", "service", "reason_for_visit", "recipient_emails"],
    "cancel": ["appointment_id"],
    "check_status": ["phone_or_name"],
    "check_availability": ["date"],
}

ACTIONABLE_INTENTS = frozenset(REQUIRED_FIELDS.keys())


class ConversationState(TypedDict, total=False):
    """State for the orchestrator graph."""

    messages: list[dict[str, Any]]
    user_intent: str
    collected_fields: dict[str, Any]
    pending_confirmation: bool
    tool_result: Optional[dict[str, Any]]
    next_action: str


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

_llm = ChatOpenAI(model="gpt-4o", temperature=0)


def _coerce_recipient_email_list(val: Any) -> list[str]:
    """Turn collected field (comma list or JSON array string) into addresses."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if "@" in str(x).strip()]
    s = str(val or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            data = json.loads(s)
            if isinstance(data, list):
                return [str(x).strip() for x in data if "@" in str(x)]
        except json.JSONDecodeError:
            return []
    return [p.strip() for p in s.split(",") if "@" in p.strip()]


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


def _get_last_user_message(state: ConversationState) -> str:
    """Extract the latest user message text from state."""
    messages = state.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, dict):
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "user" and content:
                return content if isinstance(content, str) else str(content)
        elif isinstance(m, (list, tuple)) and len(m) >= 2:
            if m[0] == "user":
                return m[1] if isinstance(m[1], str) else str(m[1])
    return ""


def classify_intent(state: ConversationState) -> dict[str, Any]:
    """Classify user intent from the latest message using a lightweight OpenAI call."""
    last_msg = _get_last_user_message(state)
    if not last_msg.strip():
        return {"user_intent": "general_query", "next_action": "respond"}

    system = """You are an intent classifier for a Gujarati voice AI assistant.
Classify the user's message into exactly one of these intents:
- book_appointment: user wants to schedule/book an appointment
- check_status: user wants to check appointment status, look up customer, or get account info
- cancel: user wants to cancel an appointment
- check_availability: user wants to know available slots for a date
- general_query: general question, greeting, or unclear request
- escalate: user wants to speak to a human, is frustrated, or needs help beyond the bot

Respond with ONLY the intent name, nothing else."""

    try:
        response = _llm.invoke(
            [{"role": "system", "content": system}, {"role": "user", "content": last_msg}]
        )
        intent = (response.content or "").strip().lower()
        # Normalize to known intents
        if intent not in ACTIONABLE_INTENTS and intent not in ("general_query", "escalate"):
            intent = "general_query"
    except Exception as e:
        logger.warning("orchestrator_intent_classify_failed", error=str(e))
        intent = "general_query"

    if intent == "escalate":
        return {"user_intent": "escalate", "next_action": "escalate"}
    if intent in ACTIONABLE_INTENTS:
        return {"user_intent": intent, "next_action": "check_slots"}
    return {"user_intent": "general_query", "next_action": "respond"}


def check_slots(state: ConversationState) -> dict[str, Any]:
    """Determine if all required fields are collected for the intent."""
    intent = state.get("user_intent") or "general_query"
    collected = state.get("collected_fields") or {}
    required = REQUIRED_FIELDS.get(intent, [])

    missing = [f for f in required if not collected.get(f)]
    if missing:
        return {"next_action": "ask_for_slot", "collected_fields": collected}
    return {"next_action": "confirm_action", "collected_fields": collected}


def ask_for_slot(state: ConversationState) -> dict[str, Any]:
    """Return a prompt asking the user for the next missing field."""
    intent = state.get("user_intent") or "general_query"
    collected = state.get("collected_fields") or {}
    required = REQUIRED_FIELDS.get(intent, [])

    missing = [f for f in required if not collected.get(f)]
    if not missing:
        return {"next_action": "confirm_action"}

    prompt_map = {
        "name": "What is your name?",
        "date": "What date would you like? (e.g., tomorrow or a specific date)",
        "time": "What time works for you?",
        "service": "What service do you need? (e.g., dental checkup, consultation)",
        "reason_for_visit": "What is the reason for this visit? (e.g. tooth pain, routine cleaning, follow-up)",
        "recipient_emails": (
            "What email address(es) should receive the booking confirmation? "
            "Comma-separated if more than one."
        ),
        "appointment_id": "What is your appointment ID?",
        "phone_or_name": "What is your phone number or name?",
    }
    field = missing[0]
    prompt = prompt_map.get(field, f"Please provide {field}.")
    return {"next_action": "respond", "tool_result": {"response": prompt}}


def confirm_action(state: ConversationState) -> dict[str, Any]:
    """Set pending_confirmation and return a confirmation prompt."""
    intent = state.get("user_intent") or "general_query"
    collected = state.get("collected_fields") or {}

    if intent == "book_appointment":
        prompt = (
            f"Please confirm: Book appointment for {collected.get('name', '')} "
            f"on {collected.get('date', '')} at {collected.get('time', '')} "
            f"for {collected.get('service', '')}. "
            f"Reason: {collected.get('reason_for_visit', '')}. "
            f"Confirmation to: {collected.get('recipient_emails', '')}. Say yes to confirm."
        )
    elif intent == "cancel":
        prompt = f"Please confirm: Cancel appointment {collected.get('appointment_id', '')}. Say yes to confirm."
    elif intent == "check_status":
        prompt = f"Please confirm: Look up customer {collected.get('phone_or_name', '')}. Say yes to confirm."
    elif intent == "check_availability":
        prompt = f"Please confirm: Check availability for {collected.get('date', '')}. Say yes to confirm."
    else:
        prompt = "Please confirm to proceed."

    return {
        "pending_confirmation": True,
        "next_action": "execute_action",
        "tool_result": {"response": prompt},
    }


async def execute_action(state: ConversationState) -> dict[str, Any]:
    """Run mock backend actions (same stubs as LiveKit tools)."""
    intent = state.get("user_intent") or "general_query"
    collected = state.get("collected_fields") or {}

    if intent == "book_appointment":
        recipient_list = _coerce_recipient_email_list(collected.get("recipient_emails"))
        data = stub_book_appointment(
            str(collected.get("name") or ""),
            str(collected.get("date") or ""),
            str(collected.get("time") or ""),
            str(collected.get("service") or ""),
            reason_for_visit=str(collected.get("reason_for_visit") or ""),
            recipient_emails=recipient_list,
        )
    elif intent == "cancel":
        data = stub_cancel_appointment(str(collected.get("appointment_id") or ""))
    elif intent == "check_status":
        data = stub_lookup_customer(str(collected.get("phone_or_name") or ""))
    elif intent == "check_availability":
        data = stub_check_availability(str(collected.get("date") or ""))
    else:
        return {"tool_result": {"error": "Unknown intent"}, "next_action": "respond"}

    logger.info("orchestrator_execute_action_mock", intent=intent)
    return {"tool_result": {"data": data, "response": str(data)}, "next_action": "respond"}


def respond(state: ConversationState) -> dict[str, Any]:
    """Format the final response from tool_result."""
    tool_result = state.get("tool_result") or {}
    if "response" in tool_result:
        return {"tool_result": tool_result}
    if "error" in tool_result:
        return {"tool_result": {"response": tool_result["error"]}}
    if "data" in tool_result:
        return {"tool_result": {"response": str(tool_result["data"])}}
    # General query or empty: yield back to main agent
    return {"tool_result": {"response": "How can I help you today?"}}


def escalate(state: ConversationState) -> dict[str, Any]:
    """Handle escalation to human."""
    return {"tool_result": {"response": "I'll connect you with a human agent. Please hold."}}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _route_after_classify(state: ConversationState) -> Literal["check_slots", "respond", "escalate"]:
    """Route from classify_intent."""
    next_action = state.get("next_action") or "respond"
    if next_action == "escalate":
        return "escalate"
    if next_action == "check_slots":
        return "check_slots"
    return "respond"


def _route_after_check_slots(
    state: ConversationState,
) -> Literal["ask_for_slot", "confirm_action"]:
    """Route from check_slots."""
    next_action = state.get("next_action") or "ask_for_slot"
    return "confirm_action" if next_action == "confirm_action" else "ask_for_slot"


def _route_after_confirm(
    state: ConversationState,
) -> Literal["execute_action"]:
    """Route from confirm_action. In practice user confirms via voice and re-invokes."""
    return "execute_action"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_orchestrator():
    """Build and return the compiled orchestrator graph with MemorySaver checkpointer."""
    builder = StateGraph(ConversationState)

    builder.add_node("classify_intent", classify_intent)
    builder.add_node("check_slots", check_slots)
    builder.add_node("ask_for_slot", ask_for_slot)
    builder.add_node("confirm_action", confirm_action)
    builder.add_node("execute_action", execute_action)
    builder.add_node("respond", respond)
    builder.add_node("escalate", escalate)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        _route_after_classify,
        {"check_slots": "check_slots", "respond": "respond", "escalate": "escalate"},
    )
    builder.add_conditional_edges(
        "check_slots",
        _route_after_check_slots,
        {"ask_for_slot": "ask_for_slot", "confirm_action": "confirm_action"},
    )
    builder.add_conditional_edges(
        "confirm_action",
        _route_after_confirm,
        {"execute_action": "execute_action"},
    )
    builder.add_edge("ask_for_slot", END)
    builder.add_edge("execute_action", "respond")
    builder.add_edge("respond", END)
    builder.add_edge("escalate", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def run_orchestration(
    graph: Any,
    thread_id: str,
    user_message: str,
    collected_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Invoke the orchestrator graph and return the response text + updated state.

    Args:
        graph: Compiled graph from build_orchestrator()
        thread_id: Conversation thread ID (e.g. call-123)
        user_message: Latest user message
        collected_fields: Optional pre-collected slot-filling data

    Returns:
        {"response": str, "state": dict}
    """
    config = {"configurable": {"thread_id": thread_id}}
    initial: ConversationState = {
        "messages": [{"role": "user", "content": user_message}],
        "collected_fields": collected_fields or {},
        "pending_confirmation": False,
        "tool_result": None,
        "next_action": "",
    }

    result = await graph.ainvoke(initial, config)
    tool_result = result.get("tool_result") or {}
    response_text = tool_result.get("response", "I'm not sure how to help with that.")

    return {
        "response": response_text,
        "state": {
            "user_intent": result.get("user_intent"),
            "collected_fields": result.get("collected_fields", {}),
            "pending_confirmation": result.get("pending_confirmation", False),
            "tool_result": result.get("tool_result"),
        },
    }
