# Twilio SIP Inbound Calls + Call Transfer — Design Spec

**Date:** 2026-03-30
**Branch:** `feature/twilio-sip-inbound-calls`
**Status:** Approved

---

## Goal

Enable the Gujarati Voice AI agent to receive real phone calls via Twilio SIP trunking, and support cold (blind) and warm (briefed) call transfers to other phone numbers (doctors, emergency line, front desk).

## Non-Goals

- Outbound calling (agent initiates calls to patients)
- IVR menu / DTMF navigation
- Call recording
- LiveKit Cloud migration

---

## Architecture

```
Phone caller dials Twilio number (+1XXXXXXXXXX)
        |
        v
  Twilio Elastic SIP Trunk
        | SIP INVITE
        v
  livekit/sip container (port 5060 + RTP 10000-20000)
        |
        v
  Redis 7 (SIP session state)
        |
        v
  livekit-server (existing, port 7880)
        | Creates SIP participant in new room "call-{uuid}"
        v
  voice-agent (GujaratiVoiceAgent via "inbound-agent" entrypoint)
        | Talks to caller, can transfer
        v
  Phone caller hears Gujarati AI agent
```

### Transfer Flows

**Cold Transfer (blind):**
```
Caller <-> Agent  ->  LiveKit TransferSIPParticipant API  ->  Caller <-> Target
           (exits)
```
- Agent confirms with caller, then transfers immediately
- Use case: emergency line, front desk, known doctor

**Warm Transfer (briefed):**
```
Step 1: Caller <-> Agent                  (caller stays in room)
Step 2: Agent creates outbound SIP call   (doctor joins same room, caller muted)
Step 3: Agent briefs doctor               ("Patient Rajesh, tooth pain since 2 days")
Step 4: Agent unmutes caller, exits room  (Caller <-> Doctor)
```
- Agent calls target number via `CreateSIPParticipant` (outbound) into the same room
- Agent briefs the doctor with patient context
- Agent then unmutes caller and leaves

---

## Infrastructure Changes

### docker-compose.yml — 2 new services

```yaml
redis:
  image: redis:7-alpine
  restart: unless-stopped

sip-service:
  image: livekit/sip
  network_mode: host
  environment:
    SIP_CONFIG_BODY: |
      log_level: info
      api_key: ${LIVEKIT_API_KEY}
      api_secret: ${LIVEKIT_API_SECRET}
      ws_url: ws://localhost:7880
      redis:
        address: localhost:6379
  depends_on:
    - livekit-server
    - redis
```

### livekit.yaml — add Redis

```yaml
redis:
  address: localhost:6379
```

### Port requirements (host firewall)

| Port | Protocol | Purpose |
|------|----------|---------|
| 5060 | UDP/TCP | SIP signaling |
| 10000-20000 | UDP | RTP media |

Must be reachable from the public internet (Twilio sends SIP INVITE to your server).

### LiveKit SIP config (applied once via `lk` CLI)

**sip/inbound-trunk.json:**
```json
{
  "trunk": {
    "name": "twilio-inbound",
    "numbers": ["+1XXXXXXXXXX"],
    "krispEnabled": true
  }
}
```

**sip/dispatch-rule.json:**
```json
{
  "rule": {
    "dispatchRuleIndividual": {
      "roomPrefix": "call-"
    }
  },
  "name": "inbound-calls"
}
```

Applied via:
```bash
lk sip create-inbound-trunk sip/inbound-trunk.json
lk sip create-dispatch-rule sip/dispatch-rule.json
```

### Outbound trunk (for warm transfer dialing out)

**sip/outbound-trunk.json:**
```json
{
  "trunk": {
    "name": "twilio-outbound",
    "address": "<twilio-sip-domain>.pstn.twilio.com",
    "numbers": ["+1XXXXXXXXXX"],
    "auth_username": "<twilio-sip-username>",
    "auth_password": "<twilio-sip-password>"
  }
}
```

Applied via:
```bash
lk sip create-outbound-trunk sip/outbound-trunk.json
```

---

## Code Changes

### config.py — new env vars

```python
# SIP / Telephony
SIP_ENABLED: bool
TRANSFER_EMERGENCY_NUMBER: str       # e.g. "+919876543210"
TRANSFER_DOCTOR_NUMBERS: dict        # JSON: {"dr_patel": "+91XXX", "dr_shah": "+91XXX"}
SIP_OUTBOUND_TRUNK_ID: str           # LiveKit outbound trunk ID for warm transfers
```

### agent.py — new SIP entrypoint

Keep existing `entrypoint()` (no `agent_name`, browser auto-dispatch).

Add second entrypoint for SIP inbound calls:

```python
@server.rtc_session("inbound-agent")
async def sip_entrypoint(ctx: agents.JobContext) -> None:
    # Extract caller info from SIP participant attributes
    # sip.callerNumber, sip.callID, sip.trunkPhoneNumber

    # Create session with caller metadata
    session = GujaratiAgentSession(
        conversation_id=ctx.room.name,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=GujaratiVoiceAgent(),  # same agent, same prompt
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )
```

### tools.py — 2 new transfer tools

**`transfer_call_cold`:**
- Args: `target` (enum: "emergency", "front_desk", "dr_patel", "dr_shah", or raw phone number)
- Confirms with caller in Gujarati before transferring
- Calls LiveKit `TransferSIPParticipant` API
- Returns transfer status

**`transfer_call_warm`:**
- Args: `target`, `briefing` (what to tell the target about the patient)
- Mutes caller audio
- Creates outbound SIP call to target via `CreateSIPParticipant` into same room
- Agent briefs target with patient context
- Unmutes caller, agent exits
- Returns transfer status

### System prompt additions

```
- જ્યારે દર્દી ગંભીર દર્દ, સોજો, અથવા ઈમરજન્સી જણાવે → confirm + cold transfer to emergency number
- જ્યારે દર્દી ચોક્કસ ડૉક્ટર સાથે વાત કરવા માંગે → warm transfer (brief the doctor first)
- ટ્રાન્સફર પહેલાં હંમેશાં દર્દીને જણાવો: "હું તમને ડૉક્ટર સાથે જોડું છું, એક મિનિટ રાહ જુઓ"
- ટ્રાન્સફર tools માં target names: "emergency", "front_desk", "dr_patel", "dr_shah"
```

---

## Twilio Setup (Manual, not code)

1. Sign up / log in at twilio.com
2. Buy a phone number (US local or toll-free)
3. Create an Elastic SIP Trunk
4. Add origination URI: `sip:YOUR_SERVER_PUBLIC_IP:5060;transport=udp`
5. Point the phone number's voice config to the SIP trunk
6. For outbound (warm transfer): create a SIP domain + credential list

---

## .env additions

```env
# SIP / Telephony
SIP_ENABLED=true
TRANSFER_EMERGENCY_NUMBER=+919876543210
TRANSFER_DOCTOR_NUMBERS={"dr_patel": "+919876543211", "dr_shah": "+919876543212"}
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxxx

# Twilio (reference only — config is in LiveKit SIP trunk, not in agent code)
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX
```

---

## Testing

1. **Unit tests:** Mock LiveKit SIP API calls in transfer tools
2. **Integration test:** Call Twilio number → verify agent answers in Gujarati
3. **Transfer test:** Trigger cold transfer → verify caller is connected to target
4. **Warm transfer test:** Trigger warm transfer → verify briefing happens before connect

---

## File Changes Summary

| File | Change |
|------|--------|
| `docker-compose.yml` | Add `redis` + `sip-service` containers |
| `livekit.yaml` | Add `redis` config block |
| `config.py` | Add SIP + transfer env vars |
| `agent.py` | Add `sip_entrypoint` with `agent_name="inbound-agent"` |
| `tools.py` | Add `transfer_call_cold` + `transfer_call_warm` tools |
| `.env.example` | Add SIP/transfer/Twilio vars |
| `sip/inbound-trunk.json` | New — LiveKit inbound trunk config |
| `sip/outbound-trunk.json` | New — LiveKit outbound trunk config |
| `sip/dispatch-rule.json` | New — LiveKit dispatch rule |
| `context.md` | Update with SIP/transfer docs |
| `tests/test_transfer.py` | New — transfer tool tests |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Server not publicly accessible (no public IP) | Use ngrok or deploy to a VPS with public IP for SIP ports |
| Twilio can't reach SIP service | Verify firewall rules, test with `sip:IP:5060` from Twilio console |
| Warm transfer timing (doctor doesn't answer) | Timeout after 30s, return to caller with apology |
| Multiple concurrent calls | Each call gets its own room (`call-{uuid}`), agent auto-dispatches per room |
