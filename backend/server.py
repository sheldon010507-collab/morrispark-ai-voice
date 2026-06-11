from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import signal
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets

from morris.config import Settings, get_settings
from morris.intent import detect_intent
from morris.knowledge import KnowledgeBase, guarded_reply
from morris.lead_store import LeadDraft, LeadStore, update_lead_from_text
from morris.voicechat import MockVoiceChatAdapter, NvidiaWebSocketVoiceChatAdapter, QwenRealtimeVoiceChatAdapter, VoiceChatAdapter


settings = get_settings()
knowledge = KnowledgeBase(settings.knowledge_dir)
lead_store = LeadStore(settings.data_dir / "leads.sqlite")


class StaticAndApiHandler(BaseHTTPRequestHandler):
    server_version = "MorrisVoiceGuide/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "voicechat_backend": settings.voicechat_backend,
                    "qwen_realtime_model": settings.qwen_realtime_model,
                    "ws_url": f"ws://{settings.ws_host}:{settings.ws_port}",
                    "public_ws_url": settings.public_ws_url,
                }
            )
            return

        if parsed.path == "/api/persona":
            self._json({"persona_prompt": knowledge.persona_prompt()})
            return

        if parsed.path == "/api/leads":
            query = parse_qs(parsed.query)
            password = self.headers.get("X-Demo-Password") or query.get("password", [""])[0]
            if password != settings.demo_password:
                self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            self._json({"leads": lead_store.recent()})
            return

        self._static(parsed.path)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[http] {self.address_string()} - {format % args}")

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, request_path: str) -> None:
        clean_path = request_path.lstrip("/") or "index.html"
        file_path = (settings.frontend_dir / clean_path).resolve()
        if not str(file_path).startswith(str(settings.frontend_dir.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or file_path.is_dir():
            file_path = settings.frontend_dir / "index.html"

        content_type, _ = mimetypes.guess_type(str(file_path))
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@dataclass
class ClientSession:
    websocket: Any
    authenticated: bool = False
    lead: LeadDraft = field(default_factory=LeadDraft)
    adapter: VoiceChatAdapter | None = None

    async def send(self, payload: dict[str, Any] | bytes) -> None:
        if isinstance(payload, bytes):
            await self.websocket.send(payload)
        else:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def start_adapter(self) -> None:
        persona = knowledge.persona_prompt()
        if settings.voicechat_backend == "nvidia-ws":
            self.adapter = NvidiaWebSocketVoiceChatAdapter(settings, persona, self.send)
        elif settings.voicechat_backend == "qwen-realtime":
            self.adapter = QwenRealtimeVoiceChatAdapter(settings, persona, self.send)
        else:
            self.adapter = MockVoiceChatAdapter(self.send)
        await self.adapter.start()

    async def close(self) -> None:
        if self.adapter is not None:
            await self.adapter.close()


async def websocket_handler(websocket: Any) -> None:
    session = ClientSession(websocket=websocket)
    await session.send({"type": "server.hello", "backend": settings.voicechat_backend})

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if session.authenticated and session.adapter is not None:
                    try:
                        await session.adapter.send_audio(message)
                    except Exception as exc:
                        print(f"[ws:audio-error] {exc}")
                        await session.send({"type": "voicechat.error", "message": str(exc)})
                continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await session.send({"type": "error", "message": "Invalid JSON message."})
                continue

            await handle_client_message(session, payload)
    except websockets.ConnectionClosed:
        pass
    finally:
        await session.close()


async def handle_client_message(session: ClientSession, payload: dict[str, Any]) -> None:
    message_type = payload.get("type")

    if message_type == "session.start":
        if payload.get("password") != settings.demo_password:
            await session.send({"type": "auth.failed", "message": "Wrong demo password."})
            await session.websocket.close()
            return
        session.authenticated = True
        await session.send(
            {
                "type": "session.started",
                "persona_prompt": knowledge.persona_prompt(),
                "backend": settings.voicechat_backend,
            }
        )
        await session.start_adapter()
        return

    if not session.authenticated:
        await session.send({"type": "auth.required"})
        return

    if message_type == "interrupt":
        await session.send({"type": "interruption.ack"})
        if session.adapter is not None:
            try:
                await session.adapter.send_control({"type": "interrupt"})
            except Exception as exc:
                print(f"[ws:interrupt-error] {exc}")
                await session.send({"type": "voicechat.error", "message": str(exc)})
        return

    if message_type == "user.transcript":
        text = str(payload.get("text", "")).strip()
        is_final = bool(payload.get("final", False))
        if text:
            await handle_transcript(session, text, is_final)
        return

    if message_type == "context.request":
        query = str(payload.get("query", "")).strip()
        await send_rag_context(session, query, force=True)
        return

    await session.send({"type": "debug.unhandled", "payload": payload})


async def handle_transcript(session: ClientSession, text: str, is_final: bool) -> None:
    intent = detect_intent(text)
    await session.send(
        {
            "type": "intent.detected",
            "text": text,
            "final": is_final,
            "intent": intent.label,
            "needs_rag": intent.needs_rag,
            "is_sensitive": intent.is_sensitive,
            "is_booking": intent.is_booking,
        }
    )

    if not is_final:
        if intent.needs_rag:
            asyncio.create_task(send_rag_context(session, text, force=False))
        return

    if intent.is_booking or session.lead.active:
        prompt = update_lead_from_text(session.lead, text)
        if session.lead.next_missing() is None and not session.lead.saved:
            lead_id = lead_store.save(session.lead)
            session.lead.saved = True
            await session.send({"type": "lead.saved", "id": lead_id})

        await inject_guidance(session, prompt, reason="lead_capture")
        if settings.voicechat_backend == "mock":
            await session.send({"type": "assistant.text", "text": prompt, "reason": "lead_capture"})
        return

    if intent.needs_rag:
        context = await send_rag_context(session, text, force=True)
        if intent.is_sensitive:
            guidance = (
                "Pricing or legal-term question detected. Only use public pricing from the knowledge base. "
                "Do not invent rent, deposits, discounts, lease lengths, or live availability. Offer to pass "
                "the enquiry to the Morris Park team."
            )
            await inject_guidance(session, guidance, reason="guardrail")

        if settings.voicechat_backend == "mock":
            await session.send(
                {
                    "type": "assistant.text",
                    "text": guarded_reply(text, knowledge),
                    "reason": "rag" if context else "fallback",
                }
            )


async def send_rag_context(session: ClientSession, query: str, force: bool) -> str:
    context = knowledge.context_for(query)
    if not context and not force:
        return ""
    await session.send({"type": "rag.context", "query": query, "context": context})
    if context:
        await inject_guidance(session, context, reason="rag_context")
    return context


async def inject_guidance(session: ClientSession, guidance: str, reason: str) -> None:
    payload = {
        "type": "context.update",
        "reason": reason,
        "text": guidance,
        "instructions": "Use this context for the next spoken response. Keep the answer short and natural.",
    }
    await session.send({"type": "guidance.injected", "reason": reason, "text": guidance})
    if session.adapter is not None:
        if not session.adapter.supports_context_update:
            return
        try:
            await session.adapter.send_control(payload)
        except Exception as exc:
            print(f"[ws:guidance-error] {exc}")
            await session.send({"type": "voicechat.error", "message": str(exc)})


def start_http_server() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((settings.http_host, settings.http_port), StaticAndApiHandler)
    thread = threading.Thread(target=httpd.serve_forever, name="http-server", daemon=True)
    thread.start()
    return httpd


async def main() -> None:
    httpd = start_http_server()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async with websockets.serve(websocket_handler, settings.ws_host, settings.ws_port, max_size=None):
        print(f"HTTP: http://{settings.http_host}:{settings.http_port}")
        print(f"WebSocket: ws://{settings.ws_host}:{settings.ws_port}")
        print(f"VoiceChat backend: {settings.voicechat_backend}")
        await stop_event.wait()

    httpd.shutdown()


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1])
    asyncio.run(main())
