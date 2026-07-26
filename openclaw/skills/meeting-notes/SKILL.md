---
name: meeting-notes
description: Turn raw meeting input into structured notes, extract action items with named owners and real dates, and file the page into Notion.
metadata:
  {
    "openclaw":
      {
        "emoji": "📝",
        "requires": { "config": ["mcp.servers.notion"] },
      },
  }
---

# Meeting notes

Input is whatever your user gives you: a pasted transcript, voice-dictated
fragments in `#capture`, a photo of a whiteboard, or three bullet points typed
while walking out of the room. Output is always the same structure, filed in the
same place.

The value here is not the summary. It's that **every commitment made in the room
comes out with a name and a date attached.** A tidy summary with vague action
items is worse than messy notes, because it looks finished.

## Before you write anything

Resolve these. If you can't, leave the placeholder visibly broken (`[WHO?]`) —
never guess.

| Field | How you get it |
|---|---|
| Title | From the calendar event if one matches the time; else ask |
| Date/time | The meeting's, not now's |
| Attendees | Calendar invite via `list_events` / `get_event`; else what your user said |
| Project | Match against existing Notion pages before inventing a new one |

Run `notion-search` for the project or an earlier meeting in the same series
*first*. Filing a fourth orphan page for a weekly that already has three is the
main way this skill degrades.

## Structure

```markdown
# <Meeting name> — 2026-07-26

**Attendees:** Rohan, Priya Shah (Sarvam), Anand M.
**Project:** [[Gujarati Voice Agent]]

## Decisions
- Ship the mock-tools build to the clinic pilot; real CRM integration waits for Q4.
- Staying on Sarvam bulbul:v3 despite the Aug 1 price rise. Revisit if volume 3x's.

## Actions
| # | Action | Owner | Due | Source |
|---|---|---|---|---|
| 1 | Send Priya the pilot latency numbers | Rohan | 2026-07-29 | "I'll get you the p95 by Monday" |
| 2 | Confirm bulbul pricing tiers in writing | Priya | 2026-07-31 | asked, agreed |
| 3 | Add Marathi to the language matrix | [WHO?] | [WHEN?] | raised, nobody claimed it |

## Open questions
- Does the clinic want the confirmation email, or is SMS enough? — blocks action 1.

## Notes
Freeform. Only what a person rereading this in three months would need.
```

Sections are fixed. Keep an empty section with `— none` rather than deleting it;
a page with no `## Decisions` heading reads as "we didn't check" instead of
"nothing was decided."

## Extracting actions — the actual work

Scan for commitment language, not for topics. These become actions:

- "I'll…", "I can…", "let me…" → owner is the speaker
- "Can you…" + any assent → owner is the addressee
- "We should…", "someone needs to…" → **unowned**. This is the important case.
- "Let's circle back on…" → open question, not an action

Then apply, in order:

1. **Every action gets a named human.** Not "the team", not "engineering", not
   "we". If nobody claimed it in the room, write `[WHO?]` and surface it — do not
   quietly assign it to your user because they were the one talking to you.
2. **Every action gets an absolute date.** Convert "next week" → the actual
   Friday. "EOD" → today's date. "Soon", "at some point", "when we get to it" →
   `[WHEN?]`. Never invent a date to fill the column.
3. **The Source column is a quote or a note of how it arose.** This is what lets
   your user overrule you when you misread the room. Keep it to a clause.
4. **Actions are verbs with objects.** "Latency" is a topic. "Send Priya the p95
   latency numbers" is an action.
5. **Do not invent actions.** A meeting where nothing was committed produces an
   empty table and that is a correct, informative result.

Deduplicate against the previous meeting in the series: if action 3 from last
time is still open and was re-raised, carry it forward with its original date and
mark it `(carried, 2nd time)`. Repeat slippage is a signal worth showing.

## Filing

1. `notion-search` for the project / meeting series.
2. `notion-create-pages` under the parent database or page you found. If nothing
   matched, ask where it goes rather than creating a new top-level page.
3. Reply with the page URL and **just the actions table**, so your user can
   correct owners and dates immediately, in chat, while the meeting is fresh.

Tool names come from the Notion MCP server and shift between versions — confirm
yours with `openclaw mcp probe notion --json` rather than trusting the names
above.

You may create and update Notion pages without asking. You may not email,
message, or otherwise send the notes to any attendee — that is outbound, and
outbound always needs a yes from your user first.

## Failure modes to avoid

- **Summarising the discussion instead of the outcome.** Nobody rereads the
  discussion. Decisions and actions carry the page.
- **Assigning everything to your user.** They're talking to you, so they're
  salient. That's a bias, not evidence.
- **Softening ambiguity.** "Follow up with Priya" is what an unowned, undated,
  undefined action looks like after you've smoothed it. Leave it jagged.
- **Filing silently.** Always return the URL. A note your user can't find didn't
  get taken.
