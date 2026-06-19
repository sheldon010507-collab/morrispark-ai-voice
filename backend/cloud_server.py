from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from server import ClientSession, handle_client_message, knowledge, settings


app = FastAPI(title="Morris Park Voice Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class FastAPIWebSocketAdapter:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def send(self, payload: str | bytes) -> None:
        if isinstance(payload, bytes):
            await self.websocket.send_bytes(payload)
        else:
            await self.websocket.send_text(payload)

    async def close(self) -> None:
        await self.websocket.close()


@app.get("/")
async def root() -> dict[str, Any]:
    return health_payload()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return health_payload()


@app.get("/api/persona")
async def persona() -> dict[str, str]:
    return {"persona_prompt": knowledge.persona_prompt()}


@app.websocket("/")
async def websocket_root(websocket: WebSocket) -> None:
    await run_voice_session(websocket)


@app.websocket("/ws")
async def websocket_ws(websocket: WebSocket) -> None:
    await run_voice_session(websocket)


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "voicechat_backend": settings.voicechat_backend,
        "qwen_realtime_model": settings.qwen_realtime_model,
        "public_ws_url": settings.public_ws_url,
    }


async def run_voice_session(websocket: WebSocket) -> None:
    await websocket.accept()
    session = ClientSession(websocket=FastAPIWebSocketAdapter(websocket))
    await session.send({"type": "server.hello", "backend": settings.voicechat_backend})

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            audio = message.get("bytes")
            if audio is not None:
                if session.authenticated and session.adapter is not None:
                    try:
                        await session.adapter.send_audio(audio)
                    except Exception as exc:
                        print(f"[ws:audio-error] {exc}")
                        await session.send({"type": "voicechat.error", "message": str(exc)})
                continue

            text = message.get("text")
            if text is None:
                continue

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                await session.send({"type": "error", "message": "Invalid JSON message."})
                continue

            await handle_client_message(session, payload)
    except (RuntimeError, WebSocketDisconnect) as exc:
        if isinstance(exc, RuntimeError) and "disconnect message" not in str(exc):
            raise
        pass
    finally:
        await session.close()
