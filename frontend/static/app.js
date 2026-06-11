const state = {
  ws: null,
  password: "morris",
  mediaStream: null,
  mediaRecorder: null,
  audioContext: null,
  audioSource: null,
  audioProcessor: null,
  pcmRemainder: new Float32Array(0),
  outputAudioContext: null,
  nextAudioPlayTime: 0,
  recognition: null,
  connectingPromise: null,
  isLive: false,
  isSpeaking: false,
  backend: "mock",
  wsUrl: null,
};

const els = {
  orb: document.querySelector("#orb"),
  caption: document.querySelector("#caption"),
  statusDot: document.querySelector("#statusDot"),
  statusText: document.querySelector("#statusText"),
  backendBadge: document.querySelector("#backendBadge"),
  transcript: document.querySelector("#transcript"),
  ragBox: document.querySelector("#ragBox"),
  leadBox: document.querySelector("#leadBox"),
};

init();

async function init() {
  try {
    const health = await fetch("/api/health").then((res) => res.json());
    state.backend = health.voicechat_backend || "mock";
    state.wsUrl = chooseWebSocketUrl(health);
    els.backendBadge.textContent = state.backend;
    if (!state.wsUrl) {
      setStatus("Backend URL missing", false);
      setCaption("Set PUBLIC_WS_URL on Vercel to connect the realtime voice backend.");
    }
  } catch {
    setStatus("Backend unavailable", false);
  }
}

els.orb.addEventListener("click", async () => {
  if (state.isLive) {
    stopFullDuplex();
    setStatus("Paused", false);
    setCaption("Tap the orb to resume.");
    return;
  }
  await startFullDuplex();
});

async function connect() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
  if (state.connectingPromise) return state.connectingPromise;
  if (!state.wsUrl) {
    setStatus("Backend URL missing", false);
    setCaption("Set PUBLIC_WS_URL on Vercel to connect the realtime voice backend.");
    throw new Error("PUBLIC_WS_URL is required for hosted demos.");
  }

  state.connectingPromise = new Promise((resolve, reject) => {
    state.ws = new WebSocket(state.wsUrl);
    state.ws.binaryType = "arraybuffer";

    state.ws.addEventListener("open", () => {
      setStatus("Connected", true);
      sendJson({ type: "session.start", password: state.password });
      state.connectingPromise = null;
      resolve();
    });

    state.ws.addEventListener("message", handleServerMessage);
    state.ws.addEventListener("close", () => {
      setStatus("Disconnected", false);
      state.isLive = false;
      state.connectingPromise = null;
      updateVoiceState();
    });
    state.ws.addEventListener("error", () => {
      setStatus("Connection error", false);
      state.connectingPromise = null;
      reject(new Error("WebSocket connection failed"));
    });
  });

  return state.connectingPromise;
}

async function startFullDuplex() {
  await connect();
  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    addMessage("system", `Microphone unavailable: ${error.message}`);
    setStatus("Mic blocked", false);
    setCaption("Microphone permission is needed. Tap the orb and allow access.");
    return;
  }

  startAudioStreaming();
  if (state.backend === "qwen-realtime") {
    addMessage("system", "Qwen realtime is handling speech detection and transcription.");
  } else {
    startSpeechRecognition();
  }
  state.isLive = true;
  setStatus("Listening", true);
  setCaption("Listening. Just speak naturally.");
  addMessage("system", "Realtime voice session started. Interruptions are handled automatically.");
  updateVoiceState();
}

function startAudioStreaming() {
  if (state.backend === "qwen-realtime") {
    startPcmStreaming();
    return;
  }

  if (!state.mediaStream || typeof MediaRecorder === "undefined") return;

  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : "";
  state.mediaRecorder = new MediaRecorder(state.mediaStream, mimeType ? { mimeType } : undefined);

  state.mediaRecorder.addEventListener("dataavailable", async (event) => {
    if (!event.data || event.data.size === 0) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    const buffer = await event.data.arrayBuffer();
    state.ws.send(buffer);
  });

  state.mediaRecorder.start(250);
}

function startPcmStreaming() {
  if (!state.mediaStream) return;
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) {
    addMessage("system", "Web Audio is unavailable, so Qwen realtime PCM capture cannot start.");
    return;
  }

  state.audioContext = new AudioContext();
  state.audioSource = state.audioContext.createMediaStreamSource(state.mediaStream);
  state.audioProcessor = state.audioContext.createScriptProcessor(4096, 1, 1);
  const inputRate = state.audioContext.sampleRate;

  state.audioProcessor.onaudioprocess = (event) => {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    const pcm16 = floatToPcm16(resampleFloat32(input, inputRate, 16000));
    if (pcm16.byteLength > 0) state.ws.send(pcm16.buffer);
  };

  state.audioSource.connect(state.audioProcessor);
  state.audioProcessor.connect(state.audioContext.destination);
  addMessage("system", `Qwen realtime PCM capture active (${inputRate}Hz → 16000Hz).`);
}

function startSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    addMessage("system", "Browser speech recognition is unavailable. Use the text box for local RAG testing.");
    return;
  }

  state.recognition = new SpeechRecognition();
  state.recognition.continuous = true;
  state.recognition.interimResults = true;
  state.recognition.lang = "en-GB";

  state.recognition.onresult = (event) => {
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = result[0]?.transcript?.trim();
      if (!text) continue;

  if (state.isSpeaking && !result.isFinal) {
        interrupt();
      }

      sendJson({ type: "user.transcript", text, final: result.isFinal });
      if (result.isFinal) addMessage("user", text);
    }
  };

  state.recognition.onerror = (event) => {
    addMessage("system", `Speech recognition: ${event.error}`);
  };

  state.recognition.onend = () => {
    if (state.isLive) {
      try {
        state.recognition.start();
      } catch {
        return;
      }
    }
  };

  try {
    state.recognition.start();
  } catch (error) {
    addMessage("system", `Speech recognition did not start: ${error.message}`);
  }
}

function stopFullDuplex() {
  state.isLive = false;
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((track) => track.stop());
  }
  if (state.audioProcessor) {
    state.audioProcessor.disconnect();
    state.audioProcessor = null;
  }
  if (state.audioSource) {
    state.audioSource.disconnect();
    state.audioSource = null;
  }
  if (state.audioContext) {
    state.audioContext.close();
    state.audioContext = null;
  }
  if (state.recognition) {
    state.recognition.stop();
  }
  window.speechSynthesis?.cancel();
  resetOutputAudio();
  state.isSpeaking = false;
  setStatus("Connected", true);
  setCaption("Voice paused.");
  updateVoiceState();
}

function interrupt() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  window.speechSynthesis?.cancel();
  resetOutputAudio();
  state.isSpeaking = false;
  sendJson({ type: "interrupt" });
  setStatus("Listening", true);
  setCaption("I’m listening.");
  updateVoiceState();
}

function submitText(rawText, final) {
  const text = rawText.trim();
  if (!text) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    connect().then(() => submitText(text, final));
    return;
  }
  addMessage("user", text);
  setCaption(text);
  sendJson({ type: "user.transcript", text, final });
}

function handleServerMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    playAudioChunk(event.data);
    return;
  }

  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    addMessage("system", event.data);
    return;
  }

  switch (payload.type) {
    case "server.hello":
      state.backend = payload.backend || state.backend;
      els.backendBadge.textContent = state.backend;
      break;
    case "session.started":
      addMessage("system", "Session started with Morris Park persona prompt.");
      break;
    case "voicechat.ready":
      setStatus("Listening", true);
      setCaption("Listening. Ask me about Morris Park.");
      if (payload.model) els.backendBadge.textContent = `${payload.backend}:${payload.model}`;
      updateVoiceState();
      break;
    case "auth.failed":
      addMessage("system", payload.message || "Authentication failed.");
      setStatus("Auth failed", false);
      setCaption("Demo password failed. Check DEMO_PASSWORD in .env.");
      break;
    case "intent.detected":
      if (payload.final) {
        setStatus("Thinking", true);
      }
      break;
    case "rag.context":
      els.ragBox.textContent = payload.context || "No matching Morris Park knowledge found.";
      break;
    case "guidance.injected":
      addMessage("system", `Injected ${payload.reason} guidance.`);
      break;
    case "assistant.text":
      addMessage("ai", payload.text);
      setCaption(payload.text);
      speak(payload.text);
      break;
    case "lead.saved":
      els.leadBox.textContent = `Lead saved locally with id ${payload.id}.`;
      setCaption("I’ve saved that for the Morris Park team.");
      break;
    case "voicechat.error":
    case "error":
      addMessage("system", payload.message || "Unknown backend error.");
      stopFullDuplex();
      setStatus("Tap to retry", false);
      setCaption("The voice session paused. Tap the orb to reconnect.");
      break;
    default:
      handleForwardedNvidiaMessage(payload);
  }
}

function handleForwardedNvidiaMessage(payload) {
  if (payload.type === "session.updated") {
    addMessage("system", "Qwen session updated with Morris Park persona.");
    setCaption("Listening. Ask me about Morris Park.");
    return;
  }

  if (payload.type === "input_audio_buffer.speech_started") {
    if (state.isSpeaking) {
      resetOutputAudio();
      sendJson({ type: "interrupt" });
    }
    state.isSpeaking = false;
    setStatus("Listening", true);
    setCaption("I’m listening.");
    updateVoiceState();
    return;
  }

  if (payload.type === "input_audio_buffer.speech_stopped") {
    setStatus("Thinking", true);
    setCaption("Thinking...");
    updateVoiceState();
    return;
  }

  if (payload.type === "conversation.item.input_audio_transcription.completed") {
    const text = payload.transcript || "";
    if (text) {
      addMessage("user", text);
      setCaption(text);
      sendJson({ type: "user.transcript", text, final: true });
    }
    return;
  }

  if (payload.type === "response.audio_transcript.delta") {
    setStatus("Speaking", true);
    return;
  }

  if (payload.type === "response.audio_transcript.done") {
    const text = payload.transcript || "";
    if (text) {
      addMessage("ai", text);
      setCaption(text);
    }
    return;
  }

  if (payload.type === "response.audio.delta" && payload.delta) {
    playPcmBase64(payload.delta, 24000);
    return;
  }

  const text =
    payload.text ||
    payload.transcript ||
    payload.message ||
    payload.response?.text ||
    payload.output?.text ||
    "";

  const type = String(payload.type || "");
  if (text && /assistant|response|output/i.test(type)) {
    addMessage("ai", text);
    setCaption(text);
  } else if (text && /user|input|transcript/i.test(type)) {
    addMessage("user", text);
    setCaption(text);
    sendJson({ type: "user.transcript", text, final: true });
  } else if (text) {
    addMessage("system", text);
  }
}

async function playAudioChunk(arrayBuffer) {
  addMessage("system", `Received ${arrayBuffer.byteLength} bytes of VoiceChat audio.`);
}

function playPcmBase64(base64, sampleRate) {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  if (!state.outputAudioContext) {
    state.outputAudioContext = new AudioContext({ sampleRate });
    state.nextAudioPlayTime = state.outputAudioContext.currentTime;
  }

  const audioBuffer = state.outputAudioContext.createBuffer(1, samples.length, sampleRate);
  const channel = audioBuffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) {
    channel[i] = Math.max(-1, Math.min(1, samples[i] / 32768));
  }

  const source = state.outputAudioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(state.outputAudioContext.destination);
  const startAt = Math.max(state.outputAudioContext.currentTime, state.nextAudioPlayTime);
  source.start(startAt);
  state.nextAudioPlayTime = startAt + audioBuffer.duration;
  state.isSpeaking = true;
  setStatus("Speaking", true);
  updateVoiceState();
  source.onended = () => {
    if (state.outputAudioContext && state.outputAudioContext.currentTime >= state.nextAudioPlayTime - 0.05) {
      state.isSpeaking = false;
      if (state.isLive) setStatus("Listening", true);
      updateVoiceState();
    }
  };
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-GB";
  utterance.rate = 1.02;
  utterance.onstart = () => {
    state.isSpeaking = true;
    setStatus("Speaking", true);
    updateVoiceState();
  };
  utterance.onend = () => {
    state.isSpeaking = false;
    if (state.isLive) setStatus("Listening", true);
    updateVoiceState();
  };
  window.speechSynthesis.speak(utterance);
}

function resetOutputAudio() {
  if (state.outputAudioContext) {
    state.outputAudioContext.close();
    state.outputAudioContext = null;
  }
  state.nextAudioPlayTime = 0;
}

function sendJson(payload) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify(payload));
}

function setStatus(text, live) {
  els.statusText.textContent = text;
  els.statusDot.classList.toggle("live", Boolean(live));
}

function setCaption(text) {
  els.caption.textContent = text;
}

function updateVoiceState() {
  els.orb.classList.toggle("listening", state.isLive && !state.isSpeaking);
  els.orb.classList.toggle("speaking", state.isSpeaking);
}

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  const label = role === "ai" ? "AI Guide" : role === "user" ? "Visitor" : "System";
  node.innerHTML = `<strong>${escapeHtml(label)}</strong>${escapeHtml(text)}`;
  els.transcript.appendChild(node);
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function chooseWebSocketUrl(health) {
  if (health.public_ws_url) return health.public_ws_url;
  const isLocalPage = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  if (isLocalPage && health.ws_url) return health.ws_url;
  return null;
}

function resampleFloat32(input, inputRate, outputRate) {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const position = i * ratio;
    const index = Math.floor(position);
    const fraction = position - index;
    const sampleA = input[index] || 0;
    const sampleB = input[index + 1] || sampleA;
    output[i] = sampleA + (sampleB - sampleA) * fraction;
  }
  return output;
}

function floatToPcm16(floatSamples) {
  const pcm = new Int16Array(floatSamples.length);
  for (let i = 0; i < floatSamples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, floatSamples[i]));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return pcm;
}
