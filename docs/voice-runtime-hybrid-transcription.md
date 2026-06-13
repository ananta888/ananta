# Voice Runtime Hybrid Transcription

Die Voice Runtime bleibt ein separater Runtime-Service unter `voice_runtime/`. Der Hub nutzt `agent/services/voice_provider.py` nur als Adapter fuer Policy, Auth und Delegation. Audioverarbeitung, Backend-Auswahl und Pipeline-Stages liegen bewusst in `voice_runtime`, damit die Hub-Control-Plane keine Worker- oder Runtime-Arbeit uebernimmt.

## Bestehende Endpunkte

Runtime-intern:

- `GET /health`: Runtime-, Backend- und Pipeline-Status.
- `GET /v1/models`: verfuegbare Voice-Modelle und optionale Backend-Adapter.
- `POST /v1/audio/transcriptions`: Multipart-Audio zu Transcript.
- `POST /v1/audio/chat`: Multipart-Audio zu Voice-Command-Text.

Hub-seitig:

- `GET /v1/voice/capabilities`
- `POST /v1/voice/transcribe`
- `POST /v1/voice/command`
- `POST /v1/voice/goal`

Der Hub bleibt die Policy- und Exposure-Grenze. `VOICE_STORE_AUDIO` bleibt fail-closed; Roh-Audio wird nicht dauerhaft gespeichert.

## Pipeline-Konfiguration

`VOICE_TRANSCRIPTION_PIPELINE` steuert die Runtime-Variante:

- `simple`: kompatibler Pfad ueber den bestehenden Backend-Router.
- `oldschool_light`: VAD plus leichtes ASR wie Vosk/Kaldi-Stil.
- `whisper_cpp`: VAD plus optionaler `whisper.cpp` Adapter.
- `realtime_streaming`: vorbereitete Streaming-Variante; Streaming wird nur mit `VOICE_ENABLE_STREAMING=true` aktiviert.
- `meeting`: ASR plus optionale Diarization.
- `confidence_rerun`: guenstiger erster Pass plus Rerun unsicherer Segmente.
- `custom`: konfigurierbarer VAD/ASR/Postprocess-Pfad.

Neue Runtime-Variablen:

- `VOICE_VAD_BACKEND`
- `VOICE_ASR_BACKEND`
- `VOICE_POSTPROCESS_BACKEND`
- `VOICE_CONFIDENCE_RERUN_ENABLED`
- `VOICE_CONFIDENCE_THRESHOLD`
- `VOICE_RERUN_BACKEND`
- `VOICE_RERUN_MAX_SEGMENTS`
- `VOICE_DIARIZATION_BACKEND`
- `VOICE_GLOSSARY_PATH`
- `VOICE_VOSK_MODEL_PATH`
- `VOICE_WHISPER_CPP_BIN`
- `VOICE_WHISPER_CPP_MODEL_PATH`
- `VOICE_WHISPER_CPP_EXTRA_ARGS`

Bestehende Variablen bleiben gueltig: `VOICE_RUNTIME_URL`, `VOICE_MODEL`, `VOICE_FALLBACK_MODEL`, `VOICE_BACKEND_FALLBACK_ORDER`, `VOICE_ENABLE_STREAMING`, `VOICE_STORE_AUDIO`.

## Backends

Der bestehende Router in `voice_runtime/backends/router.py` bleibt die Kompatibilitaetsgrenze und unterstuetzt additiv `mock`, `voxtral`, `vosk` und `whisper_cpp`.

`VoxtralBackend` ist weiterhin ein deterministischer MVP-Adapter. Echte Modellverdrahtung kann intern ersetzt werden, ohne die Runtime-API zu aendern.

`VoskBackend` und `WhisperCppBackend` sind optionale Adapter. Fehlende Dependencies, Modellpfade oder Binaries lassen den Runtime-Start nicht fehlschlagen; die Adapter melden `unavailable`, und der Router kann gemaess `VOICE_BACKEND_FALLBACK_ORDER` auf `mock` fallen.

## Ergebnisvertrag

`POST /v1/audio/transcriptions` behaelt die bisherigen Felder:

- `provider`
- `model`
- `text`
- `language`
- `duration_ms`
- `warnings`

Additiv kommen Pipeline-Felder hinzu:

- `segments`
- `pipeline`
- `confidence`
- `raw_backend`
- `rerun_backend`
- `stages`

Jede Pipeline-Stufe ist maschinenlesbar in `stages` sichtbar, z.B. `vad`, `asr`, `confidence_rerun`, `diarization`, `postprocess`.

## SOLID-Check

- SRP: VAD, ASR-Adapter, Glossary, Postprocessing, Diarization und Orchestrator sind getrennte Module.
- OCP: Neue Backends werden ueber Adapter und Router ergaenzt, ohne bestehende Endpunkte umzubenennen.
- DIP: Routen haengen an `TranscriptionPipeline`/`VoiceBackend`-Abstraktionen statt an konkreter Modelllogik.
- ISP: Der Hub-Adapter reicht nur HTTP-Ergebnisfelder weiter und kennt keine Audio-Engine-Details.
