# Text-Quality: Operations-Guide

Was Operatoren über das Text-Quality-Subsystem wissen müssen.

## Konfiguration

`text_quality.*` (unter `AGENT_CONFIG`):

| Key                            | Default       | Bedeutung                                             |
|--------------------------------|---------------|-------------------------------------------------------|
| `enabled`                      | `false`       | Feature-Gate für die gesamte Text-Quality             |
| `evaluate_planning_outputs`    | `false`       | Plant-Aufgaben-Texte in Evaluation einbeziehen         |
| `llm_judge_enabled`            | `false`       | Optionaler LLM-Judge                                  |
| `rewrite_enabled`              | `false`       | Reserved for future proposals (no edit-in-place)      |
| `default_profile`              | `critical_editor_de` | Profilname für language=fallback                |
| `max_input_chars`              | 12000         | harter Request-Limit                                  |
| `max_input_words`              | 2500          | harter Wort-Limit                                     |
| `max_output_bytes`             | 262144        | Sandbox-Output-Limit                                  |
| `max_slop_score`               | 0.35          | Evolution-Trigger                                     |
| `min_depth_score`              | 0.7           | Evolution-Trigger                                     |
| `min_canary_runs`              | 10            | Cohort-Größe für Text-Quality-Triggers                |
| `external_detectors.avoid_ai_writing.enabled` | `false` | Sandbox-Provider-Gate       |
| `…execution_plane`             | `sandbox`     | nicht konfigurierbar                                  |
| `…network`                     | `none`        | nicht konfigurierbar                                  |
| `…pinned_commit`               | null          | explizit pin, sonst Provider bleibt disabled          |
| `…detector_sha256`             | null          | Pflicht für Aktivierung                               |
| `…context_mode`                | `technical`   | `general` / `technical`                               |

## Betriebsmodi

### Feature-off (Default)

Keine Auswertung, keine Sandbox, kein LLM-Aufruf. Bestehende
Planning- und Evolution-Tests laufen byte-kompatibel durch.

### Scanner-only

`text_quality.enabled=true` ohne weitere Flags. Der Core-Scanner
läuft, der LLM-Judge nicht, der Sandbox-Provider nicht. Nützlich
für Kostenfreiheit und um Kalibration zu beobachten.

### LLM-Judge aktiv

`llm_judge_enabled=true`. Der Judge erhält einen bounded Prompt,
nutzt JSON-Schema, `temperature=0`, maximal einen Repair-Versuch.
Ausfall liefert `degraded`, **niemals** `completed`.

### External Sandbox-Provider aktiv

`text_quality.external_detectors.avoid_ai_writing.enabled=true` und
Pin/Checksum gesetzt. Aufruf erfolgt **ausschließlich** über die
`SandboxBackend`-Schnittstelle mit fester argv-Liste
`[node, /workspace/bridge.mjs]`.

Der Bundle-Pfad (`detector_path`/`bridge_path`) wird zur Laufzeit
aufgelöst; bei Pfad-Traversal oder Pfad außerhalb des approved bundle
liefert der Provider `provider_unavailable`, ohne den Detector zu
laden.

### Degraded-Fallback

Bei Sandbox-Timeout, Nonzero-Exit, Outputlimit-Überschreitung,
Checksum-Mismatch oder fehlendem Node liefert der Provider
`DetectorSignal(status=DEGRADED, degraded_reason="…")`. Der
Evaluator behandelt das als **fehlenden** Provider und gewichtet
die Fusion entsprechend — keine Score-0-Imputation.

## Upstream-Pin und Checksum-Update

Bei Upstream-Änderungen:

1. Audit (siehe `docs/integrations/avoid-ai-writing.md`).
2. Checksum neu berechnen.
3. `detector_sha256` in `AGENT_CONFIG.text_quality.external_detectors` setzen.
4. Re-Run von `tests/test_text_quality_calibration.py` und
   `tests/test_avoid_ai_writing_provider.py`.
5. Snapshot-Review der `KNOWN_UPSTREAM_TYPES`-Liste (44 Typen am
   gepinnten Commit).

## Offline-CI / Reproduzierbarkeit

- Keine Netzwerk-Aufrufe während `pytest`.
- Bundle wird vorab als Read-Only-Filesystem-Mount oder Pre-Commit
  deployt.
- `scripts/check_cli_backend_shim_imports.py` und
  `scripts/validate_cross_track_dependencies.py` laufen weiterhin grün.
- CI Gate: alle Tests in
  `tests/test_text_quality_*.py`,
  `tests/test_avoid_ai_writing_*.py`,
  `tests/test_anti_slop_evolution_provider.py`,
  `tests/test_planning_*_text_quality.py`,
  `tests/test_text_quality_e2e.py`.

## Häufige Failure-Modes

| Symptom                                            | Ursache / Lösung                                          |
|----------------------------------------------------|------------------------------------------------------------|
| Provider `degraded_reason=provider_checksum_mismatch` | Upstream-Code geändert — Audit-Pfad neu starten         |
| `text_too_short` für einen ganzen Task             | Core-Scanner-Mindestlänge (10 Wörter)                       |
| `text_too_long`                                    | `max_input_chars` zu klein konfiguriert                    |
| `upstream_unknown:type`                             | neue Detector-Kategorie — Snapshot-Review                  |
| `source_unverified` in Plan-Tasks                  | Evidence-IDs fehlen oder sind ungleich                     |
| Cohort nicht vergleichbar (`text_quality_comparable=False`) | verschiedene `criteria_version`/`evaluator_version` | `criterion_confidence`-Gate |
| `Rollback wiederholt, aber Provider stable`         | Cohort-Drift zuerst ausschließen, dann Konfig prüfen      |
