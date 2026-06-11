from __future__ import annotations

import asyncio
import json
import os
import ssl
from pathlib import Path
from urllib.parse import urlencode

import certifi
import websockets


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = [
    "qwen3.5-omni-plus-realtime",
    "qwen3.5-omni-flash-realtime",
    "qwen3-omni-flash-realtime",
    "qwen-omni-turbo-realtime",
]


def load_env() -> dict[str, str]:
    values = dict(os.environ)
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values


async def try_model(api_key: str, endpoint: str, model: str, voice: str) -> bool:
    separator = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{separator}{urlencode({'model': model})}"
    print(f"\nTrying {model}")
    try:
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=15,
            max_size=None,
            ssl=ssl.create_default_context(cafile=certifi.where()),
        ) as ws:
            print("Connected")
            await ws.send(
                json.dumps(
                    {
                        "event_id": "test_session_update",
                        "type": "session.update",
                        "session": {
                            "modalities": ["text", "audio"],
                            "voice": voice,
                            "input_audio_format": "pcm",
                            "output_audio_format": "pcm",
                            "instructions": "You are a concise Morris Park voice guide. Say hello briefly.",
                            "turn_detection": {"type": "server_vad", "threshold": 0.5, "silence_duration_ms": 800},
                        },
                    }
                )
            )
            for _ in range(8):
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                event = json.loads(raw)
                print(event.get("type"), json.dumps(event, ensure_ascii=False)[:500])
                if event.get("type") == "session.updated":
                    print("Success: session.updated")
                    return True
                if event.get("type") == "error":
                    return False
    except Exception as exc:
        print(f"Failed: {exc}")
    return False


async def main() -> None:
    env = load_env()
    api_key = env.get("DASHSCOPE_API_KEY", "")
    endpoint = env.get("QWEN_REALTIME_ENDPOINT", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime")
    preferred_model = env.get("QWEN_REALTIME_MODEL", "").strip()
    voice = env.get("QWEN_REALTIME_VOICE", "Tina").strip() or "Tina"

    if not api_key:
        print("DASHSCOPE_API_KEY is missing. Add it to .env or export it in the terminal.")
        return

    models = [preferred_model] if preferred_model else []
    models.extend(model for model in DEFAULT_MODELS if model not in models)

    for model in models:
        if await try_model(api_key, endpoint, model, voice):
            print(f"\nUse this in .env:\nQWEN_REALTIME_MODEL={model}")
            return
    print("\nNo realtime model connected. Check the API key region and Model Studio permissions.")


if __name__ == "__main__":
    asyncio.run(main())
