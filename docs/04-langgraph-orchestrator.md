# LangGraph Orchestrator

The orchestrator in `orchestrator.py` is an **optional addition** on top of the LiveKit voice pipeline. It handles multi-step conversation flows that require remembering state across multiple turns — like collecting all the details needed to book an appointment before making the API call.

## Why it exists

GPT-4o with function tools handles simple one-shot actions well ("what time is it?", "cancel appointment APT-123"). But for flows that require gathering multiple pieces of information in sequence, a state machine works better than relying on GPT-4o to remember what it has and hasn't asked yet.

The orchestrator handles:
- **Slot filling** — collecting name, date, time, and service before booking
- **Confirmation** — reading back details before committing an action
- **Escalation** — routing to a human when the user is frustrated
- **Error recovery** — retrying after a failed action (when you add real backends)

## Graph structure

```
START
  │
  ▼
classify_intent
  │
  ├──(book/cancel/check)──► check_slots
  │                              │
  │                    ┌─────────┴─────────┐
  │               missing fields       all fields collected
  │                    │                    │
  │                    ▼                    ▼
  │              ask_for_slot        confirm_action
  │                    │                    │
  │                    ▼                    ▼
  │                  END            execute_action
  │                                        │
  ├──(general_query)──────────────►  respond
  │                                        │
  ├──(escalate)──────────────────►  escalate
  │                                        │
  └─────────────────────────────────────► END
```

## Nodes explained

### `classify_intent`
Makes a lightweight GPT-4o call to classify the user's message into one of:
- `book_appointment` — user wants to schedule something
- `check_status` — user wants to look up their account or appointment
- `cancel` — user wants to cancel
- `check_availability` — user wants to see available slots
- `general_query` — anything else
- `escalate` — user is frustrated or wants a human

### `check_slots`
Checks whether all required fields have been collected for the detected intent:

| Intent | Required fields |
|--------|----------------|
| `book_appointment` | name, date, time, service |
| `cancel` | appointment_id |
| `check_status` | phone_or_name |
| `check_availability` | date |

If any field is missing → routes to `ask_for_slot`.
If all fields present → routes to `confirm_action`.

### `ask_for_slot`
Returns a natural language prompt for the first missing field:
- `name` → "What is your name?"
- `date` → "What date would you like?"
- `time` → "What time works for you?"
- `service` → "What service do you need?"
- `appointment_id` → "What is your appointment ID?"

### `confirm_action`
Reads back all collected information before executing:
> "Please confirm: Book appointment for Rajesh Patel on February 25th at 10am for a dental checkup. Say yes to confirm."

Sets `pending_confirmation = True` in state.

### `execute_action`
Runs the same **mock** helpers as `tools.py` (no HTTP): `stub_book_appointment`, `stub_cancel_appointment`, `stub_lookup_customer`, `stub_check_availability` depending on intent.

### `respond`
Formats the tool result into a natural language response.

### `escalate`
Returns: "I'll connect you with a human agent. Please hold." (You can extend this to actually transfer the call.)

## State schema

```python
class ConversationState(TypedDict, total=False):
    messages: list[dict]          # conversation history
    user_intent: str              # classified intent
    collected_fields: dict        # slot data accumulated so far
    pending_confirmation: bool    # waiting for user to say "yes"
    tool_result: dict             # result from execute_action
    next_action: str              # routing signal between nodes
```

State persists across turns using LangGraph's `MemorySaver` checkpointer, keyed by `thread_id` (the LiveKit room name).

## Using the orchestrator from a tool call

The orchestrator is designed to be called from within a tool or directly from the agent. Example:

```python
from orchestrator import build_orchestrator, run_orchestration

# Build once at startup
graph = build_orchestrator()

# Call during a conversation turn
result = await run_orchestration(
    graph,
    thread_id=ctx.room.name,          # persists state per call
    user_message="I want to book an appointment for tomorrow",
    collected_fields={},               # pass any already-known data
)

# result["response"] is the text to speak to the user
# result["state"] has the updated slot data for the next turn
print(result["response"])
# → "What date would you like?"
```

## Checkpointing

The orchestrator uses `MemorySaver` (in-process memory). For production, swap this to a persistent checkpointer:

```python
from langgraph.checkpoint.postgres import PostgresSaver

# Connect to your Postgres instance
checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost:5432/langgraph"
)
graph = builder.compile(checkpointer=checkpointer)
```

This survives agent restarts and lets you inspect conversation state in the database.
