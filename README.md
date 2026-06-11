# Morris Park VoiceChat + Async RAG Demo

Local demo for a full-duplex Morris Park AI voice guide. The app keeps the main
experience as realtime voice while running a local async RAG and lead-capture
sidecar.

## Run

```bash
cp .env.example .env
python3 backend/server.py
```

Open:

```text
http://localhost:5173
```

The default `VOICECHAT_BACKEND=mock` mode uses browser speech recognition and
browser speech synthesis so you can test the UI, RAG, interruption behavior, and
SQLite lead capture without spending API credits.

## Alibaba Cloud / Qwen Realtime Mode

Set these in `.env`:

```text
VOICECHAT_BACKEND=qwen-realtime
DASHSCOPE_API_KEY=...
QWEN_REALTIME_ENDPOINT=wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime
QWEN_REALTIME_MODEL=qwen3.5-omni-plus-realtime
QWEN_REALTIME_VOICE=Cherry
```

Test model access first:

```bash
python3 backend/test_qwen_realtime.py
```

Fallback model order:

```text
qwen3.5-omni-plus-realtime
qwen3.5-omni-flash-realtime
qwen3-omni-flash-realtime
qwen-omni-turbo-realtime
```

In Qwen mode, the browser sends 16 kHz PCM audio to the backend. The backend
forwards it to DashScope using `input_audio_buffer.append`, receives
`response.audio.delta`, and plays 24 kHz PCM audio in the browser.

## NVIDIA VoiceChat Mode

Set these in `.env`:

```text
VOICECHAT_BACKEND=nvidia-ws
NVIDIA_API_KEY=...
NVIDIA_VOICECHAT_WS_URL=wss://...
NVIDIA_VOICECHAT_FUNCTION_ID=...
```

If your Early Access endpoint expects a specific session payload, set:

```text
NVIDIA_VOICECHAT_SESSION_JSON={...}
```

The backend sends a `session.start` control message with the Morris Park persona
prompt and forwards browser audio chunks to the NVIDIA WebSocket. JSON messages
and binary audio from NVIDIA are forwarded back to the browser.

## Project Shape

```text
backend/
  server.py                 # local HTTP + WebSocket server
  morris/
    config.py               # .env loading
    intent.py               # lightweight intent rules
    knowledge.py            # local RAG + persona prompt
    lead_store.py           # SQLite lead capture
    voicechat.py            # mock and NVIDIA WebSocket adapters
frontend/static/
  index.html
  styles.css
  app.js
knowledge/
  core_facts.json
  availability.json
  guardrails.json
  faq.md
data/
  leads.sqlite              # created on first run
```

## Demo Checks

- Interrupt while the AI is speaking: browser TTS stops and sends `interrupt`.
- Ask: `Do you have parking?`
- Ask: `Where is Morris Park?`
- Ask: `What spaces are available?`
- Ask: `How much is it?`
- Say: `I want to book a viewing`

Remote demos need HTTPS for microphone access. Use Cloudflare Tunnel or ngrok
and keep the password enabled.
