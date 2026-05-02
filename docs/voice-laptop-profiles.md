# Voice Runtime Laptop Profiles

Diese Profile helfen beim schnellen Start auf Laptops, ohne Hub-Worker-Architektur zu verletzen.

## CPU-only laptop (safe default)

```bash
VOICE_RUNTIME_DEVICE=cpu
VOICE_MODEL=voxtral
VOICE_FALLBACK_MODEL=whisper-small
VOICE_BACKEND_FALLBACK_ORDER=voxtral,mock
VOICE_TIMEOUT_SEC=120
VOICE_MAX_AUDIO_MB=25
```

- Empfohlen fuer Notebooks ohne dedizierte GPU.
- Bei Zeitouts fallback auf `mock` aktiv lassen.

## GPU laptop (NVIDIA/eGPU)

```bash
VOICE_RUNTIME_DEVICE=cuda
VOICE_MODEL=voxtral
VOICE_FALLBACK_MODEL=whisper-small
VOICE_BACKEND_FALLBACK_ORDER=voxtral,mock
VOICE_TIMEOUT_SEC=180
VOICE_MAX_AUDIO_MB=25
```

- Setze `CUDA_VISIBLE_DEVICES` passend im Host.
- Bei VRAM-Engpaessen `VOICE_BACKEND_FALLBACK_ORDER=mock` als Notfallpfad nutzen.

## Shared LAN GPU host + thin laptop

```bash
VOICE_RUNTIME_URL=http://<gpu-host>:8090
VOICE_DIRECT_CLIENT_ACCESS=false
VOICE_STORE_AUDIO=false
```

- Hub bleibt Policy- und Audit-Eigentümer.
- Clients sprechen weiterhin nur mit dem Hub.
