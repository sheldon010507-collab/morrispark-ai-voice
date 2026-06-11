from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    root: Path
    frontend_dir: Path
    knowledge_dir: Path
    data_dir: Path
    demo_password: str
    voicechat_backend: str
    nvidia_api_key: str
    nvidia_voicechat_ws_url: str
    nvidia_voicechat_function_id: str
    nvidia_voicechat_session_json: dict[str, Any]
    dashscope_api_key: str
    qwen_realtime_endpoint: str
    qwen_realtime_model: str
    qwen_realtime_voice: str
    http_host: str
    http_port: int
    ws_host: str
    ws_port: int
    public_ws_url: str


def get_settings() -> Settings:
    load_env()
    session_json: dict[str, Any] = {}
    raw_session_json = os.getenv("NVIDIA_VOICECHAT_SESSION_JSON", "").strip()
    if raw_session_json:
        session_json = json.loads(raw_session_json)

    return Settings(
        root=ROOT,
        frontend_dir=ROOT / "frontend" / "static",
        knowledge_dir=ROOT / "knowledge",
        data_dir=ROOT / "data",
        demo_password=os.getenv("DEMO_PASSWORD", "morris"),
        voicechat_backend=os.getenv("VOICECHAT_BACKEND", "mock").strip().lower(),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
        nvidia_voicechat_ws_url=os.getenv("NVIDIA_VOICECHAT_WS_URL", ""),
        nvidia_voicechat_function_id=os.getenv("NVIDIA_VOICECHAT_FUNCTION_ID", ""),
        nvidia_voicechat_session_json=session_json,
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        qwen_realtime_endpoint=os.getenv("QWEN_REALTIME_ENDPOINT", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"),
        qwen_realtime_model=os.getenv("QWEN_REALTIME_MODEL", "qwen3.5-omni-plus-realtime"),
        qwen_realtime_voice=os.getenv("QWEN_REALTIME_VOICE", "Cherry"),
        http_host=os.getenv("HTTP_HOST", "127.0.0.1"),
        http_port=int(os.getenv("HTTP_PORT", "5173")),
        ws_host=os.getenv("WS_HOST", "127.0.0.1"),
        ws_port=int(os.getenv("WS_PORT", "8001")),
        public_ws_url=os.getenv("PUBLIC_WS_URL", ""),
    )
