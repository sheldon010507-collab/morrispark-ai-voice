from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = {
            "ok": True,
            "voicechat_backend": os.getenv("VOICECHAT_BACKEND", "mock"),
            "qwen_realtime_model": os.getenv("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime"),
            "ws_url": "",
            "public_ws_url": os.getenv("PUBLIC_WS_URL", ""),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
