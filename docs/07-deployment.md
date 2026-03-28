# Deployment

## Local development

### Requirements
- Python 3.10+
- Node.js 18+ (for the React frontend)
- An existing virtual environment at `research/.venv`

### Start everything

```bash
# Terminal 1 — Voice agent
cd research
.venv/bin/python agent.py download-files  # first time only
.venv/bin/python agent.py dev             # hot-reloads on file changes

# Terminal 2 — React frontend
cd frontend
npm run dev
# Open http://localhost:3000
```

The `dev` command connects your local agent to LiveKit Cloud. Any user who connects to your LiveKit project will be served by your local agent instance.

### Console mode (no browser needed)

```bash
cd research
.venv/bin/python agent.py console
```

Lets you speak to the agent directly from your terminal using your microphone. Useful for rapid testing without opening a browser.

## Docker (self-hosted LiveKit + agent)

The `docker-compose.yml` runs LiveKit and the voice agent locally (tools remain mocks):

```bash
cd research
cp .env.example .env
# Fill in your API keys in .env

docker compose up -d
```

| Service | Port | Purpose |
|---------|------|---------|
| `livekit-server` | 7880, 7881 | LiveKit WebRTC server |
| `voice-agent` | — | The Python voice agent |

### LiveKit server config
`livekit.yaml` configures the self-hosted LiveKit server:
```yaml
port: 7880
rtc:
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: false
  tcp_port: 7881
```

For production behind a load balancer or with external IPs, set `use_external_ip: true`.

## Production deployment (cloud VM)

### Recommended setup
1. **LiveKit Cloud** — managed WebRTC (free tier available) is the simplest path
2. **VM for agent** — deploy the Python agent to any VM (AWS EC2, GCP, DigitalOcean Droplet)

### Build and deploy the agent

```bash
# Build the Docker image
docker build -t gujarati-voice-agent .

# Run on your VM
docker run -d \
  --env-file .env \
  --restart unless-stopped \
  gujarati-voice-agent
```

### Environment variables for cloud

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
SARVAM_API_KEY=...
OPENAI_API_KEY=...
LOG_LEVEL=INFO
```

### Scaling

LiveKit agents scale horizontally — run multiple instances and LiveKit Cloud will load-balance across them. This project uses the default worker registration (no `agent_name` on `@server.rtc_session`) so participants get an agent automatically when they join a room; add `agent_name` only if you switch to explicit dispatch and put matching `RoomAgentDispatch` on join tokens or use the Agent Dispatch API.

## Running tests

```bash
cd research
.venv/bin/python -m pytest tests/ -v
```

Expected output:
```
tests/test_tagged_speech.py::test_plain_text_no_tags          PASSED
tests/test_tagged_speech.py::test_no_interrupt_tag            PASSED
tests/test_tagged_speech.py::test_mute_tag                    PASSED
tests/test_tagged_speech.py::test_mixed_content               PASSED
tests/test_tagged_speech.py::test_nested_tags                 PASSED
tests/test_tagged_speech.py::test_empty_input_yields_nothing  PASSED
tests/test_tagged_speech.py::test_partial_xml_tags_buffer...  PASSED
tests/test_tools.py::...                                       PASSED
tests/test_orchestrator.py::...                                PASSED (7 tests)
```

## Monitoring

Logs are structured JSON (when `LOG_LEVEL=INFO`). Key events to watch:

| Log event | Meaning |
|-----------|---------|
| `session_started` | User connected |
| `session_closed` | Session ended, includes duration and transcript count |
| `transcript_closed` | Full transcript payload logged (mock-tool build) |
| `tool_*` | Mock tool invocation (e.g. `tool_book_appointment`) |

Pipe logs to any log aggregator (Datadog, Grafana Loki, CloudWatch, etc.):

```bash
docker run ... gujarati-voice-agent 2>&1 | your-log-shipper
```
