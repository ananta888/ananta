# Voice Quickstart (Hub + voice-runtime)

Dieser Quickstart zeigt den minimalen Weg fuer Sprachpfade ueber den Hub.

## 1) Voice Overlay starten

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.voice-runtime.yml up -d --build
```

## 2) Defaults in `.env` setzen

```bash
VOICE_PROVIDER=voice-runtime
VOICE_RUNTIME_URL=http://voice-runtime:8090
VOICE_MODEL=voxtral
VOICE_FALLBACK_MODEL=whisper-small
VOICE_BACKEND_FALLBACK_ORDER=voxtral,mock
VOICE_MAX_AUDIO_MB=25
VOICE_TIMEOUT_SEC=120
VOICE_STORE_AUDIO=false
```

`VOICE_STORE_AUDIO=false` ist der Privacy-Default und bleibt fail-closed.

## 3) Hub Capability pruefen

```bash
curl -s -H "Authorization: Bearer <TOKEN>" http://localhost:5000/v1/voice/capabilities
```

Erwartung:
- `available` ist `true` oder (bei Runtime-Problemen) kontrolliert `false`.
- `privacy.raw_audio_persisted` bleibt `false`.

## 4) Transcribe / Command / Goal pruefen

```bash
curl -s -H "Authorization: Bearer <TOKEN>" -F "file=@sample.webm" http://localhost:5000/v1/voice/transcribe
curl -s -H "Authorization: Bearer <TOKEN>" -F "file=@sample.webm" http://localhost:5000/v1/voice/command
curl -s -H "Authorization: Bearer <TOKEN>" -F "file=@sample.webm" -F "approved=true" http://localhost:5000/v1/voice/goal
```

Goal-Erzeugung ohne `approved=true` wird policy-basiert geblockt.

## 5) Optionaler CLI-Pfad

```bash
ananta voice-file ./sample.webm --mode transcribe
ananta voice-file ./sample.webm --mode command
ananta voice-file ./sample.webm --mode goal --approved --create-tasks
```

## 6) Optional: Smoke + Live Tests

```bash
RUN_VOICE_DOCKER_SMOKE=1 pytest tests/smoke/test_voice_runtime_compose_smoke.py
RUN_LIVE_VOXTRAL_TESTS=1 LIVE_VOXTRAL_RUNTIME_URL=http://localhost:8090 pytest tests/test_voice_runtime_live_voxtral.py
```

Weiterfuehrende Hinweise:
- `docs/voice-runtime-privacy.md`
- `docs/voice-runtime-limitations.md`
