from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    env = read_env()
    api_key = env.get("NVIDIA_API_KEY", "")
    if not api_key:
        print("NVIDIA_API_KEY is missing in .env")
        return

    result = subprocess.run(
        [
            "curl",
            "-sS",
            "-H",
            f"Authorization: Bearer {api_key}",
            "https://integrate.api.nvidia.com/v1/models",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print("NVIDIA model check failed:")
        print(result.stderr.strip())
        return

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("NVIDIA returned a non-JSON response:")
        print(result.stdout[:500])
        return

    model_ids = [item.get("id", "") for item in payload.get("data", [])]
    voice_matches = [model_id for model_id in model_ids if "voice" in model_id.lower()]
    nemotron_matches = [model_id for model_id in model_ids if "nemotron" in model_id.lower()]

    print(f"API key works. Visible model count: {len(model_ids)}")
    print("Voice model matches:")
    print(json.dumps(voice_matches, indent=2))
    print("Nemotron model matches:")
    print(json.dumps(nemotron_matches, indent=2))
    if "nvidia/nemotron-voicechat" not in model_ids:
        print()
        print("nvidia/nemotron-voicechat is not visible through /v1/models for this key.")
        print("Get NVIDIA_VOICECHAT_WS_URL and NVIDIA_VOICECHAT_FUNCTION_ID from the logged-in")
        print("build.nvidia.com/nvidia/nemotron-voicechat Try API / Code panel.")


if __name__ == "__main__":
    main()
