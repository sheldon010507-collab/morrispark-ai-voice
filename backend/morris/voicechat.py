from __future__ import annotations

import asyncio
import base64
import ssl
import json
from urllib.parse import urlencode
from typing import Any, Awaitable, Callable

import certifi
import websockets

from .config import Settings


SendToClient = Callable[[dict[str, Any] | bytes], Awaitable[None]]


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


class VoiceChatAdapter:
    async def start(self) -> None:
        raise NotImplementedError

    async def send_audio(self, data: bytes) -> None:
        raise NotImplementedError

    async def send_control(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    @property
    def supports_context_update(self) -> bool:
        return True


class MockVoiceChatAdapter(VoiceChatAdapter):
    def __init__(self, send_to_client: SendToClient):
        self.send_to_client = send_to_client

    async def start(self) -> None:
        await self.send_to_client(
            {
                "type": "voicechat.ready",
                "backend": "mock",
                "message": "Mock VoiceChat ready. Browser speech recognition and synthesis are active.",
            }
        )

    async def send_audio(self, data: bytes) -> None:
        return None

    async def send_control(self, payload: dict[str, Any]) -> None:
        if payload.get("type") == "interrupt":
            await self.send_to_client({"type": "voicechat.interrupted"})

    async def close(self) -> None:
        return None


class NvidiaWebSocketVoiceChatAdapter(VoiceChatAdapter):
    def __init__(self, settings: Settings, persona_prompt: str, send_to_client: SendToClient):
        self.settings = settings
        self.persona_prompt = persona_prompt
        self.send_to_client = send_to_client
        self.ws: Any = None
        self.reader_task: asyncio.Task[None] | None = None
        self.closed = False

    async def start(self) -> None:
        if not self.settings.nvidia_voicechat_ws_url:
            raise RuntimeError("NVIDIA_VOICECHAT_WS_URL is required when VOICECHAT_BACKEND=nvidia-ws")
        if not self.settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY is required when VOICECHAT_BACKEND=nvidia-ws")

        headers = {
            "Authorization": f"Bearer {self.settings.nvidia_api_key}",
        }
        if self.settings.nvidia_voicechat_function_id:
            headers["NVCF-FUNCTION-ID"] = self.settings.nvidia_voicechat_function_id
            headers["function-id"] = self.settings.nvidia_voicechat_function_id

        self.ws = await websockets.connect(
            self.settings.nvidia_voicechat_ws_url,
            additional_headers=headers,
            max_size=None,
            ssl=ssl_context(),
        )

        session_payload: dict[str, Any] = {
            "type": "session.start",
            "prompt": self.persona_prompt,
            "audio": {
                "input": "webm_opus",
                "output": "pcm_or_encoded",
            },
            "response": {
                "modalities": ["audio", "text"],
                "interruptible": True,
            },
        }
        session_payload.update(self.settings.nvidia_voicechat_session_json)
        await self.ws.send(json.dumps(session_payload))
        self.reader_task = asyncio.create_task(self._read_loop())
        await self.send_to_client({"type": "voicechat.ready", "backend": "nvidia-ws"})

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    await self.send_to_client(message)
                    continue

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = {"type": "nvidia.raw", "message": message}
                await self.send_to_client(payload)
        except Exception as exc:
            await self.send_to_client({"type": "voicechat.error", "message": str(exc)})

    async def send_audio(self, data: bytes) -> None:
        if self.ws is not None:
            await self.ws.send(data)

    async def send_control(self, payload: dict[str, Any]) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps(payload))

    async def close(self) -> None:
        if self.reader_task is not None:
            self.reader_task.cancel()
        if self.ws is not None:
            await self.ws.close()


class QwenRealtimeVoiceChatAdapter(VoiceChatAdapter):
    def __init__(self, settings: Settings, persona_prompt: str, send_to_client: SendToClient):
        self.settings = settings
        self.persona_prompt = persona_prompt
        self.send_to_client = send_to_client
        self.ws: Any = None
        self.reader_task: asyncio.Task[None] | None = None

    @property
    def supports_context_update(self) -> bool:
        # Qwen Realtime's conversation.item.create currently accepts only
        # function_call_output. Morris Park facts live in session instructions
        # until we add proper Qwen tool-calling.
        return False

    async def start(self) -> None:
        if not self.settings.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required when VOICECHAT_BACKEND=qwen-realtime")

        separator = "&" if "?" in self.settings.qwen_realtime_endpoint else "?"
        url = f"{self.settings.qwen_realtime_endpoint}{separator}{urlencode({'model': self.settings.qwen_realtime_model})}"
        headers = {
            "Authorization": f"Bearer {self.settings.dashscope_api_key}",
        }

        self.ws = await websockets.connect(url, additional_headers=headers, max_size=None, ssl=ssl_context())
        self.closed = False
        self.reader_task = asyncio.create_task(self._read_loop())

        await self.send_control(
            {
                "event_id": "morris_session_update",
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "voice": self.settings.qwen_realtime_voice,
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "instructions": self.persona_prompt,
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "silence_duration_ms": 800,
                    },
                },
            }
        )
        await self.send_to_client(
            {
                "type": "voicechat.ready",
                "backend": "qwen-realtime",
                "model": self.settings.qwen_realtime_model,
                "voice": self.settings.qwen_realtime_voice,
            }
        )

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    await self.send_to_client(message)
                    continue

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = {"type": "qwen.raw", "message": message}
                if payload.get("type") in {"error", "conversation.item.input_audio_transcription.failed"}:
                    print("[qwen:error]", json.dumps(payload, ensure_ascii=False))
                    self.closed = True
                elif payload.get("type") in {
                    "session.created",
                    "session.updated",
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "response.created",
                    "response.done",
                }:
                    print("[qwen:event]", payload.get("type"))
                await self.send_to_client(payload)
        except Exception as exc:
            self.closed = True
            print(f"[qwen:closed] {exc}")
            await self.send_to_client({"type": "voicechat.error", "message": str(exc)})

    async def send_audio(self, data: bytes) -> None:
        if self.ws is None or self.closed:
            return
        await self.ws.send(
            json.dumps(
                {
                    "event_id": "audio_append",
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(data).decode("ascii"),
                }
            )
        )

    async def send_control(self, payload: dict[str, Any]) -> None:
        if self.ws is None or self.closed:
            return

        if payload.get("type") == "interrupt":
            await self.ws.send(json.dumps({"event_id": "morris_cancel", "type": "response.cancel"}))
            return

        if payload.get("type") == "context.update":
            print("[qwen:context] skipped live context.update; Qwen requires tool-call output for conversation.item.create")
            return

        await self.ws.send(json.dumps(payload))

    async def close(self) -> None:
        if self.reader_task is not None:
            self.reader_task.cancel()
        if self.ws is not None:
            await self.ws.close()
        self.closed = True
