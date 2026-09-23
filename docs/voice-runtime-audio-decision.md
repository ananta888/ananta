# Voice Runtime: AudioDecision Specialist (whisper.cpp-audio-decision)

Optionaler, standardmaessig abgeschalteter Provider fuer typisierte Audio-Entscheidungen
(`audio.decision.v1`) aus dem Fork `ananta888/whisper.cpp-audio-decision` (Branch `audio-decision`).
Grundlage ist der Provider-Vertrag `docs/audio-decision-ananta.md` im Fork (WADEC-025).

- Provider: `voice_runtime/backends/audio_decision.py` (`AudioDecisionProvider`, `DecisionOutcome`)
- Hub-Abbildung: `agent/services/audio_decision_hub_gate.py` (`gate_audio_decision`)
- Tests: `tests/test_voice_audio_decision_provider.py`, `tests/test_voice_audio_decision_integration.py`

## Betrieb

`whisper-server` aus dem Fork laeuft als separater lokaler Prozess; Ananta spricht nur HTTP
(`POST /v1/audio/decisions`, `GET /v1/audio/decisions/profiles`), kein Linking, kein gemeinsamer Speicher.

```
whisper-server -m ggml-base.en.bin --host 127.0.0.1 --port 8081 \
  --decision-profiles examples/decision/profiles --decision-vad-model ggml-silero-v6.2.0.bin \
  --decision-api-key "$KEY" --decision-workers 2
```

| Variable | Default | Bedeutung |
|---|---|---|
| `VOICE_AUDIO_DECISION_ENABLED` | `false` | Aus: kein Provider, keine Konfiguration gelesen, kein Dienstkontakt |
| `VOICE_AUDIO_DECISION_URL` | `http://127.0.0.1:8081` | `http` nur fuer Loopback, private IPs oder Compose-Servicenamen; sonst `https` |
| `VOICE_AUDIO_DECISION_API_KEY_FILE` | - | bevorzugt: Bearer-Token aus dem Secret-Store (`/run/secrets/...`) |
| `VOICE_AUDIO_DECISION_API_KEY` | - | Alternative ohne Datei; mindestens 16 Zeichen, Pflicht wenn aktiviert |
| `VOICE_AUDIO_DECISION_TIMEOUT_MS` | `3000` | 100..60000; wird durch die `BackendCancellationToken`-Deadline weiter gesenkt |
| `VOICE_AUDIO_DECISION_PROFILES` | `speech-commands-en,home-control-en,confirm-en-de` | Allowlist; andere Profile werden lokal abgelehnt |
| `VOICE_AUDIO_DECISION_SEMANTIC_PROFILES` | `intent-semantic-experimental` | Profile mit abstrakten Intents; laufen immer mit `fallback=transcribe` |

Profile: `speech-commands-en` (beta, kalibriert fuer base.en); `home-control-en` (beta, nur synthetische
Stimmen - vor Produktiveinsatz mit echten Nutzern pruefen); `confirm-en-de` (experimentell, Sprache fest
setzen, Deutsch nicht validiert); `intent-semantic-experimental` (liefert immer `unsupported`, nur ueber
Transkript + System-2 nutzbar; muss explizit in die Allowlist).

## Ablauf und Regeln

1. Vor dem Upload: `SafeAudioDecoder` mit `AudioDecodeLimits(max_duration_ms=30000)`; Clips ueber 30 s,
   leere oder nicht dekodierbare Audios verlassen den Prozess nicht. Hochgeladen wird normalisiertes
   16-kHz-Mono-WAV.
2. Jeder Fehler (Transport, Timeout, HTTP-Fehler, kaputte Antwort, falsche `api_version`, Profil-Mismatch)
   ist `DecisionOutcome(ok=False, error_code=...)` ohne Wert.
3. Hub-Abbildung (`gate_audio_decision`):

| Ergebnis | Kind | Hub-Aktion |
|---|---|---|
| `ok=false` | `no_decision` | `normal_path` (Policy wird nicht gefragt) |
| Feld `ok`, `calibrated=true` | `proposal` | Hub-Policy: `allow` -> `act`, `confirm` -> `confirm`, `deny`/Fehler -> `deny` |
| Feld `ok`, `calibrated=false` | `ranking` | hoechstens `confirm`, nie `act` |
| `abstain` / `ambiguous` / `unsupported` | `no_value` | `ask_again` |
| kein Wert + `fallback.transcript` + `system2_required` | `system2` | `system2` mit Transkript |

- Ein Decision-Wert erteilt nie selbst eine Berechtigung (`grants_permission` ist immer `False`); `act`
  entsteht nur aus der Hub-Policy. Policy-Ausnahmen oder unbekannte Verdikte werden zu `deny`.
- `matched_from_transcript` ist ein lexikalischer Treffer fuer System-2, kein Wert.
- Provenienz wird mitgefuehrt: `model`, `profile.id/version/status`, `usage.n_encode(_total)`,
  `fallback.provenance`.
- Logs enthalten nur Profil-ID, ok/Fehlercode, Feldanzahl, `n_encode` und Latenz - kein Audio, keine
  Transkripte, keine Labels, keinen Schluessel. `HubAudioDecision.as_audit_dict()` ist transkriptfrei.

## Tests

```
docker exec -w /app compose-next-ai-agent-hub-1 python -m pytest -q tests/test_voice_audio_decision_provider.py
RUN_INTEGRATION_TESTS=1 VOICE_AUDIO_DECISION_IT_URL=... VOICE_AUDIO_DECISION_IT_API_KEY=... \
  VOICE_AUDIO_DECISION_IT_AUDIO=samples/jfk.wav python -m pytest -q tests/test_voice_audio_decision_integration.py
```

Der Integrationstest startet den Server alternativ selbst (`VOICE_AUDIO_DECISION_IT_SERVER_BIN`,
`VOICE_AUDIO_DECISION_IT_MODEL`, `VOICE_AUDIO_DECISION_IT_PROFILES_DIR`) und meldet sich ohne
Server/Modell als SKIPPED.

## Submodule

Bewusst nicht eingebunden: Ananta nutzt den Fork nur ueber HTTP, und ein erstes Submodule im Repo
(`vendor/whisper.cpp-audio-decision`) wuerde CI-Checkouts, Container-Builds und Repository-Indexer
betreffen. Bei Bedarf: `git submodule add -b audio-decision https://github.com/ananta888/whisper.cpp-audio-decision.git vendor/whisper.cpp-audio-decision`.

## Wuensche an den Fork (nicht umgesetzt, Fork unveraendert)

1. Referenz-Client `examples/decision/client/audio_decision_client.py`: bei HTTP 200 mit JSON, das kein
   Objekt ist (z. B. `[1, 2]` von einem Proxy), wirft `_outcome` `AttributeError` (`j.get("object")`) statt
   `bad_response` zu liefern; Ananta behandelt das fail-closed.
2. Der Referenz-Client liest `fallback.provenance`, `usage.n_encode_total` und `fallback.reason` nicht mit,
   obwohl der Provider-Vertrag Provenienz verlangt.
3. Der Referenz-Client prueft nicht, dass `profile.id` der Antwort dem angefragten Profil entspricht.
4. Ein Profil-Flag (z. B. `"requires_fallback": "transcribe"`) fuer semantische Profile wuerde die
   Ananta-seitige Liste `VOICE_AUDIO_DECISION_SEMANTIC_PROFILES` ersetzen.
