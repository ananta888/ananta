# Voice Runtime: AudioDecision Specialist (whisper.cpp-audio-decision)

Optionaler, standardmaessig abgeschalteter Provider fuer typisierte Audio-Entscheidungen
(`audio.decision.v1`) aus dem Fork `ananta888/whisper.cpp-audio-decision` (Branch `audio-decision`).
Grundlage ist der Provider-Vertrag `docs/audio-decision-ananta.md` im Fork (WADEC-025).

- Provider: `voice_runtime/backends/audio_decision.py` (`AudioDecisionProvider`, `DecisionOutcome`,
  Stream-Sessions `AudioDecisionStreamSession`)
- Hub-Abbildung: `agent/services/audio_decision_hub_gate.py` (`gate_audio_decision`, `gate_stream_event`)
- Hub-Policy: `agent/services/audio_decision_command_policy.py` (`VoiceCommandAudioDecisionPolicy`)
- Sprachkommando-Ablauf: `agent/services/audio_decision_command_service.py`; primaerer Pfad von
  `POST /v1/voice/command` und Fassade `POST /v1/voice/audio-decisions/command` in
  `agent/routes/voice_audio_decision.py`
- Ausfuehrung mit Bestaetigung: `agent/services/audio_decision_command_executor.py`
  (`VoiceCommandExecutor`, `VoiceCommandConfirmationStore`), Route `POST /v1/voice/command/confirm`
- Tests: `tests/test_voice_audio_decision_provider.py`, `tests/test_voice_audio_decision_command_policy.py`,
  `tests/test_voice_audio_decision_stream.py`, `tests/test_voice_audio_decision_route.py`,
  `tests/test_voice_audio_decision_execution.py`, `tests/test_voice_audio_decision_integration.py`
- Todo-Track: `todos/active/todo.whisper-audio-decision-hub-integration.json`

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

## Sprachkommando: primaerer Pfad von `/v1/voice/command`

`POST /v1/voice/command` (bestehende Route, Auth + Exposure-Operation `command` immer zuerst) nutzt die
AudioDecision als primaeren Pfad, wenn `VOICE_AUDIO_DECISION_ENABLED` an ist **und** die Anfrage ein
`decision_profile` traegt (optional `decision_field`, `decision_language`, `decision_fallback`
`transcribe`/`none`). Header `X-Ananta-Deadline-Seconds` wird zur `BackendCancellationToken`-Deadline
(sonst 30 s).

- Ohne `decision_profile` oder mit Feature aus verhaelt sich die Route exakt wie vorher: keine
  Konfiguration gelesen, kein Dienstkontakt, gleiche Antwortfelder, nur das Audit `voice_command`.
- `normal_path` (Dienst nicht erreichbar, Timeout, `unauthorized`, ...): die Route laeuft mit demselben
  Audio in ihre regulaere Voice-Runtime-Pipeline weiter; die Antwort traegt zusaetzlich
  `audio_decision: {hub_action: "normal_path", error_code, audit_id, grants_permission: false}`.
- Sonst beantwortet der Decision-Pfad die Anfrage selbst (`path: "audio_decision"`), Voice-Runtime wird
  nicht aufgerufen.
- Fehlkonfiguriert: `503 audio_decision.misconfigured` ohne Key-Material; nicht freigegebene
  Profile/Felder/Fallbacks: `422`, bevor der Dienst kontaktiert wird.

`POST /v1/voice/audio-decisions/command` bleibt als duenne Fassade ueber denselben Helfern (Felder
`profile`, `field`, `language`, `fallback`; Default-Profil `speech-commands-en`) fuer Aufrufer, die nur die
typisierte Entscheidung wollen: sie beantwortet `normal_path`, statt zu transkribieren, und antwortet bei
Feature aus `200 {enabled: false, hub_action: "normal_path", error_code: "audio_decision_disabled"}` ohne das
Audio zu lesen.

Ablauf: Audio -> `AudioDecisionProvider.decide` -> `DecisionOutcome` -> `gate_audio_decision` mit
`VoiceCommandAudioDecisionPolicy` -> `dispatch_audio_decision` (Executor/Bestaetigung) -> typisierte Antwort:

| Feld | Inhalt |
|---|---|
| `hub_action` | `act` / `confirm` / `deny` / `ask_again` / `system2` / `normal_path` |
| `decision_kind` | `proposal` / `ranking` / `no_value` / `system2` / `no_decision` |
| `action` | nur bei `act`/`confirm`: `{type, command, field, profile, confidence, requires_confirmation}` |
| `policy` | `{verdict, rule}` (Regel-ID aus der Tabelle unten; `policy_error` wenn die Policy warf) |
| `execution` | bei `act` bzw. Ablehnung durch den Executor: `{status: executed/denied, action_type, error_code, effect, grants_permission: false}` |
| `confirmation` | nur bei `confirm`: `{confirmation_id, action_type, expires_in_seconds, confirm_route, single_use: true, grants_permission: false}` |
| `system2` | nur bei `system2`: `{transcript, proposed_goal, matched_from_transcript, requires_approval: true, goal_route: "/v1/voice/goal"}` |
| `fallback_route` | bei `normal_path`: `/v1/voice/command` |
| `grants_permission` | immer `false` |

## Ausfuehrung und Bestaetigung

`VoiceCommandExecutor` (`agent/services/audio_decision_command_executor.py`) ist eine Registry
`action_type -> Handler` mit exakten Namen: `voice.control.{stop,start,switch_on,switch_off}`,
`voice.navigate.{up,down,left,right}`, `voice.dialog.{affirm,reject}`. Jeder Handler liefert eine typisierte
Client-Direktive als `effect` (z. B. `{kind: "navigate", direction: "left"}`). Ein unbekannter Aktionstyp
(auch `home.*`, Praefixe, Wildcards) wird abgelehnt (`executor.unknown_action_type`), nie geraten.

- `act` (nur aus Policy-Regel P12): unmittelbar vor dem Handler wird die Exposure-Policy (Operation `command`)
  erneut geprueft; nur ein literales `True` erlaubt die Ausfuehrung (`executor.not_authorized` sonst, auch bei
  Ausnahmen). Ein `act` ohne Policy-Verdikt `allow`, ohne Aktion oder mit `grants_permission` wird `deny`.
  Handler-Fehler -> `deny` (`executor.handler_failed`).
- `confirm`: fuer einen registrierten Aktionstyp entsteht eine Bestaetigung (`vcc-...`, 24 Zufallsbytes),
  einmal verwendbar, TTL `VOICE_AUDIO_DECISION_CONFIRM_TTL_SECONDS` (Default 30 s, begrenzt auf 5..300),
  gebunden an Tenant, Subject und die konkrete Aktion (Typ, Wert, Profil, Feld); hoechstens 256 offen. Fuer
  nicht registrierte Aktionstypen wird keine Bestaetigung ausgestellt (`deny`). Eine offene Bestaetigung ist
  nie ein allow.
- `POST /v1/voice/command/confirm` (Auth + Exposure-Operation `command`), JSON
  `{confirmation_id, action_type, confirmed}`: nur `confirmed: true` (literales Boolean) fuehrt aus,
  `false` verwirft (`confirmation.rejected`, 200), alles andere ist `422` ohne die Bestaetigung zu
  verbrauchen. Jede sonstige Verwendung verbraucht die ID: unbekannt `403 confirmation.unknown`, wiederverwendet
  `403 confirmation.already_used`, abgelaufen `410 confirmation.expired`, fremder Principal
  `403 confirmation.foreign_binding`, anderer Aktionstyp `403 confirmation.action_mismatch`.
- `deny`/`ask_again`/`normal_path`: typisierte Antworten, nichts wird ausgefuehrt. `system2`: Uebergabe an den
  Goal-Pfad `/v1/voice/goal` (dort explizite Freigabe `approved=true`); das Transkript steht nur im
  `system2`-Block der Antwort an den authentifizierten Aufrufer, nie in Audits/Logs.
- Betriebs-Invariante: der Hub laeuft als EINE manuell verwaltete Instanz (der Betreiber legt keine mehrfachen
  Replicas oder Worker-Prozesse an). Der Bestaetigungsspeicher liegt daher bewusst im Prozessspeicher (wie die
  Hub-Stream-Sessions) und braucht keinen geteilten Speicher wie Redis oder eine DB. Bestaetigungen sind
  kurzlebig und einmalig; ein Neustart/Deploy verwirft offene Bestaetigungen und fuehrt zu `deny` (fail-closed),
  nie zu `allow`. Kein offener Punkt, sondern Auslegung.

Audit (ohne Audio, Transkript, Label, Effekt, Bestaetigungs-ID): `voice_audio_decision_command`
(`HubAudioDecision.as_audit_dict()` + Profil + Policy-Regel + Aktionstyp + `execution`/`confirmation`) und
`voice_audio_decision_confirmation` (`confirmed`, `execution: {status, action_type, error_code}`).

## Hub-Policy (`VoiceCommandAudioDecisionPolicy`)

Regeln in Reihenfolge, die erste passende gewinnt:

| Regel | Bedingung | Verdikt |
|---|---|---|
| P0 | Vorschlag behauptet `grants_permission` | deny |
| P1 | weder `proposal` noch `ranking` | deny |
| P2 | Profil/Feld nicht im Kommandokatalog | deny |
| P3 | Wert ist kein bekanntes Label (Typ muss exakt passen, `1` ist nicht `true`) | deny |
| P4 | Aktionstyp fuer diese Policy-Instanz nicht freigegeben (`permitted_action_types`) | deny |
| P5 | Profilstatus unbekannt | deny |
| P6 | `ranking` (unkalibriert) | confirm |
| P7 | Vorschlag ohne Konfidenz | deny |
| P8 | experimentelles Profil oder confirm-only Profil/Feld | confirm |
| P9 | Server-Modell ist nicht das kalibrierte Modell des Profils | confirm |
| P10 | kalibrierte Konfidenz < `allow_min_confidence` (0.9) | confirm |
| P11 | Aktion braucht per Design eine Bestaetigung | confirm |
| P12 | sonst (kalibriert, sicher, direkte Aktion mit niedrigem Risiko) | allow |

Katalog: `speech-commands-en` - `stop`, `no`, `up`, `down`, `left`, `right` sind direkte Aktionen
(`voice.control.stop`, `voice.dialog.reject`, `voice.navigate.*`); `yes`, `go`, `on`, `off` bestaetigen oder
aendern Zustand und brauchen immer eine Bestaetigung. `home-control-en` (nur synthetische Stimmen) und
`confirm-en-de` (experimentell) sind confirm-only; `intent-semantic-experimental` hat keine Aktionen und
laeuft nur ueber Transkript + System-2. `allow` erlaubt nur dem Hub-Executor, eine registrierte Aktion
mit niedrigem Risiko nach erneuter Exposure-Pruefung auszufuehren; der Wert selbst ist keine Berechtigung.

## Stream-Sessions

`AudioDecisionProvider.open_stream(profile, fields, language, fallback, options=StreamOptions(...),
cancellation_token)` -> `StreamOpenResult(ok, session, error_code)`; `session.push(pcm_s16le, flush=False,
cancellation_token)` / `session.flush()` -> `StreamPushResult(ok, events, error_code, position_ms, in_speech,
closed)`; `session.close()` (idempotentes `DELETE`, auch als Kontextmanager).

- Eingabe: rohes 16-kHz-Mono-s16le-PCM (`provider.decode_pcm(...)` liefert es aus Dateien ueber
  `SafeAudioDecoder`). Ungerade Laengen und lokale Sitzungslimits (`max_audio_ms` des Servers) werden lokal
  abgelehnt; grosse Pushes werden in `max_chunk_bytes`-Stuecke (<= 1 MiB) geteilt, `flush=1` nur am letzten.
- `StreamOptions` wird lokal validiert (Schwellen in [0, 1], Dauern 0..30000 ms, `partial_every_ms` 0 oder
  >= 200); die Allowlist und der erzwungene Transcribe-Fallback fuer semantische Profile gelten wie bei
  `decide`.
- Events (`StreamEvent`): `speech_start`, `partial`, `final` (mit `debounced`), `revoked`, `dropped`; unbekannte
  Typen oder Events ohne `seq` werden ignoriert, nie geraten. Nur `partial`/`final` tragen ein
  `DecisionOutcome`; ein fehlendes oder kaputtes Ergebnis (Fehlerobjekt, falsche `api_version`, fremdes
  Profil) ist `ok=False` ohne Wert.
- `gate_stream_event`: nur ein nicht-entprelltes `final` erreicht das Hub-Gate; `partial` ist vorlaeufig,
  ein entprelltes `final` ist eine Wiederholung (`None` = keine Hub-Aktion).
- Abbruch: ein gecanceltes/abgelaufenes `BackendCancellationToken` vor oder waehrend eines Pushes verwirft
  die Ergebnisse dieses Pushes und schliesst die Sitzung (`DELETE`). Transportfehler/Timeouts und kaputte
  Antworten schliessen die Sitzung ebenfalls (VAD-Zustand unbekannt); `404` -> `session_expired`;
  `409 busy`, `413`, `429` lassen sie offen. Server ohne VAD-Modell: `not_configured`.
- Logs: nur Sitzungs-ID-Praefix (8 Zeichen), ok/Fehlercode, Anzahl Events/Finals; kein Audio, kein
  Transkript, kein Label, kein Schluessel.

## Tests

```
docker exec -w /app compose-next-ai-agent-hub-1 python -m pytest -q tests/test_voice_audio_decision_provider.py
RUN_INTEGRATION_TESTS=1 VOICE_AUDIO_DECISION_IT_URL=... VOICE_AUDIO_DECISION_IT_API_KEY=... \
  VOICE_AUDIO_DECISION_IT_AUDIO=samples/jfk.wav python -m pytest -q tests/test_voice_audio_decision_integration.py
```

Der Integrationstest startet den Server alternativ selbst (`VOICE_AUDIO_DECISION_IT_SERVER_BIN`,
`VOICE_AUDIO_DECISION_IT_MODEL`, `VOICE_AUDIO_DECISION_IT_PROFILES_DIR`, optional
`VOICE_AUDIO_DECISION_IT_VAD_MODEL` fuer Stream-Sessions) und meldet sich ohne Server/Modell als SKIPPED.

Kalibrierter ok-Fall (`speech-commands-en` auf `ggml-base.en.bin`): `VOICE_AUDIO_DECISION_IT_COMMAND_AUDIO`
zeigt auf einen Speech-Commands-v0.02-Clip aus dem Holdout-Split (die Kalibrierung nutzte die anderen
Sprecher), `VOICE_AUDIO_DECISION_IT_COMMAND_LABEL` auf dessen Label (Default `stop`). Clips liegen nicht im
Repo (Datensatz CC BY 4.0, lokal z. B. unter `whisper-decision-data/sc/`); ohne Clip wird der Test SKIPPED.
Nachweis 2026-09-23: `sc/stop/f9643d42_nohash_3.wav` -> `status=ok`, `value=stop`, `calibrated=true`,
`confidence=0.9994` -> `proposal` -> Policy `P12_direct_low_risk` -> `act` (`voice.control.stop`); derselbe
Clip ueber eine Stream-Session -> `speech_start`, `final(ok, confidence=0.9994)`.

Server fuer den Container-Lauf auf dem Host am Docker-Bridge-Gateway starten (privat, `http` erlaubt), danach
beenden:

```
whisper-server -m ggml-base.en.bin --host 172.19.0.1 --port 8081 --decision-profiles examples/decision/profiles \
  --decision-vad-model ggml-silero-v6.2.0.bin --decision-api-key "$KEY" --decision-workers 2
docker exec -w /app -e RUN_INTEGRATION_TESTS=1 -e VOICE_AUDIO_DECISION_IT_URL=http://172.19.0.1:8081 \
  -e VOICE_AUDIO_DECISION_IT_API_KEY="$KEY" -e VOICE_AUDIO_DECISION_IT_AUDIO=/tmp/wadec-it/jfk.wav \
  -e VOICE_AUDIO_DECISION_IT_COMMAND_AUDIO=/tmp/wadec-it/stop.wav compose-next-ai-agent-hub-1 \
  env -u AGENT_TOKEN_FILE python -m pytest -q tests/test_voice_audio_decision_integration.py
```

Hub-Routentests im Container brauchen `env -u AGENT_TOKEN_FILE`: das Container-Token-File kollidiert sonst
mit dem Inline-Testtoken der `app`-Fixture (HTTP 401, betrifft auch `tests/test_voice_api_contract.py`).

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
