---
name: weekly-review
description: Sunday retro plus memory curation — what shipped, what slipped, and what long-term memory absorbed, proposed as diffs for approval.
metadata: { "openclaw": { "emoji": "🔁" } }
---

# Weekly review

Runs Sundays 17:00 from cron, `--session isolated`. Two jobs in one, and the
second is the one that actually matters over months.

1. **Retro** — what happened, honestly.
2. **Memory curation** — what the assistant thinks it learned about your user,
   put in front of them before it calcifies.

Memory is the part of this system that degrades silently. A wrong fact absorbed
in March is still shaping answers in September, and nobody notices because it's
never displayed. This job displays it.

## Part 1 — Retro

Gather from the week: completed and slipped calendar items, meeting-note action
tables (`notion-search` the last 7 days), open PRs and merges if `dev` reports
them, and the past week's briefs.

```
WEEK OF 20–26 JUL

SHIPPED
- Gujarati agent pilot went live at the clinic. 41 calls, 2 escalations.
- Docker deploy runbook written down.

SLIPPED
- Marathi language matrix (action 3, meeting 21 Jul). Unowned then, unowned now.
- "Confirm bulbul pricing in writing" — Priya's, 3 days late.

PATTERN
Third week running that Thursday afternoon got eaten by unplanned calls.
Everything scheduled Thursday PM has moved at least once.

NEXT WEEK
- 2 fixed commitments, 4 open days.
- One decision needs making: Marathi in scope for the pilot or not?
```

Rules:

- **Slipped is not a moral category.** Report it flat. No encouragement, no
  disappointment. Your user can read a list.
- **One pattern per review, at most.** Only claim one if you can point at three
  instances. "You seem stressed" is not a pattern; "Thursday PM has moved three
  weeks running" is.
- **Do not re-list everything that went fine.** Shipped is a short list of things
  that finished, not an activity log.
- Under 300 words for this half.

## Part 2 — Memory curation

Pull what long-term memory absorbed this week:

```bash
openclaw memory search --since 7d          # what got written
```

or via tools: `memory_search` across the week, then `memory_get` on anything
that looks new. Read the current `MEMORY.md` in the workspace root before
proposing changes to it.

### Show what was absorbed

List every memory formed this week, in plain language, grouped:

```
MEMORY ABSORBED THIS WEEK (7 new)

Preferences        3
- Prefers 25-minute meetings over 30.
- Wants CI failures in #dev, never DM.
- Doesn't want weather in the brief unless leaving the house.

Facts              3
- Works with Priya Shah at Sarvam (vendor contact, not colleague).
- Clinic pilot is Patel Dental, Bandra.
- Uses Asia/Kolkata; standup is 14:00 IST.

Uncertain          1
- "Dislikes video calls" — inferred from one declined invite. Weak.
```

Then flag problems. Be specific about *which* memory and *why*:

| Flag | Test | Example |
|---|---|---|
| **Stale** | Was true, isn't now | "Working on the LangGraph orchestrator" — that's been shelved since June |
| **Wrong** | Contradicted by something this week | Memory says Priya is at LiveKit; Tuesday's thread says Sarvam |
| **Over-specific** | A one-off recorded as a standing rule | "Wants Marathi added" was a single meeting question, not a preference |
| **Redundant** | Two memories saying one thing | Three separate entries about preferring short meetings |
| **Leaked** | Task state or group-conversation content that shouldn't be long-term at all | "Needs to send Anand the recording" — that's an action item, not a fact about a person |

The over-specific and leaked categories are the ones that quietly poison recall.
Aggressively flag anything that reads like *state* rather than a durable fact.
The bar from `AGENTS.md` is the right one: **will this still be true and useful
in six months?**

### Propose, do not write

You do not edit `MEMORY.md` in this job. You produce a diff and stop.

```
PROPOSED MEMORY.md CHANGES — approve, edit, or say no

+ ADD
  Priya Shah — Sarvam AI, vendor contact for STT/TTS. Not a colleague.

~ CHANGE
  - "Prefers 30-minute meetings"
  + "Prefers 25-minute meetings; books 30 only for new people"

- REMOVE
  "Working on the LangGraph orchestrator"  (shelved since June)
  "Needs to send Anand the recording"      (task state, not a durable fact)

? UNCERTAIN — I'd rather you decide
  "Dislikes video calls" — one declined invite is thin evidence. Keep or drop?

Reply with the line numbers you want, or "all", or "none".
```

Then wait. If your user doesn't answer, the proposal expires — do not carry it
forward and apply it next week on the assumption that silence was assent. Re-raise
it once, next review, and then drop it.

If they approve, apply the edits to `MEMORY.md` with the `edit` tool and confirm
what changed in one line.

### Curation rules

- **Never remove a memory in the same run that you noticed it.** Removal is the
  destructive direction; it always goes through the proposal.
- **Quote the memory verbatim when flagging it.** Paraphrasing a memory you're
  about to delete makes the deletion unreviewable.
- **Say what triggered the flag.** "Contradicted by Tuesday's thread with Priya"
  is checkable. "Seems outdated" is not.
- **Cap the proposal at 10 changes.** More than that means memory needs a manual
  pass, not a weekly nibble — say so and stop.
- If nothing was absorbed and nothing is stale, say `Memory: 0 new, nothing
  stale.` and move on. Most weeks should look like that.

## Ending

Close with the one decision for next week, if there is one. No summary of the
summary, no encouragement, no "great week!". End on the open question.
