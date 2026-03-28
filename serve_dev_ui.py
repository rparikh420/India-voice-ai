#!/usr/bin/env python3
"""
Serve the browser test UI and mint LiveKit tokens from .env (no lk CLI needed).

  cd research && .venv/bin/python serve_dev_ui.py

Then open http://127.0.0.1:8765 — the page loads URL + token automatically; click Connect.

Requires agent running: python agent.py dev
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

RESEARCH_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = RESEARCH_DIR / "frontend"
DEFAULT_PORT = int(os.environ.get("DEV_UI_PORT", "8765"))


def _mint_token() -> tuple[str, str, str]:
    prev = os.getcwd()
    try:
        os.chdir(RESEARCH_DIR)
        from dotenv import load_dotenv

        load_dotenv()
        livekit_url = os.getenv("LIVEKIT_URL", "").strip()
        if not livekit_url:
            raise ValueError("LIVEKIT_URL missing in .env")
        room = (os.getenv("DEV_ROOM", "test") or "test").strip()
        identity = (os.getenv("DEV_IDENTITY", "user1") or "user1").strip()
        from livekit import api

        token = (
            api.AccessToken()
            .with_identity(identity)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
            .with_ttl(datetime.timedelta(hours=24))
            .to_jwt()
        )
        return livekit_url, token, room
    finally:
        os.chdir(prev)


class DevUIHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/dev-token":
            try:
                livekit_url, token, room = _mint_token()
                body = json.dumps(
                    {"livekit_url": livekit_url, "token": token, "room": room}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            index = FRONTEND_DIR / "index.html"
            if not index.is_file():
                self.send_error(404, "index.html not found")
                return
            data = index.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), DevUIHandler)
    print(f"Dev UI: http://127.0.0.1:{DEFAULT_PORT}/")
    print("Ensure the voice agent is running: python agent.py dev")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
