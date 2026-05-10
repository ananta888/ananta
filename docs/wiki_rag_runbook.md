# Wiki-RAG Runbook

## Presets und Formate
- Primär: Wikimedia XML.BZ2 Multistream + Index.
- Fallback: Wikimedia XML.BZ2 ohne Multistream.
- ZIM: sichtbar, aber aktuell fail-closed (unsupported).
- JSONL: internes Cache-/Austauschformat, keine primäre User-Quelle.

## Betrieb
1. Preset oder URL wählen.
2. Async Import-Job starten.
3. Jobphase prüfen (`download_parse_normalize`, `index`, `completed/failed`).
4. Bei Fehlern: `error`, `issues`, Download-/Storage-Hinweise prüfen.

## Jobsteuerung
- `pause`: kontrollierte Pause zwischen Phasen/Batches.
- `resume`: Fortsetzung aus letzter stabiler Phase.
- `cancel`: kontrollierter Abbruch; Jobstatus `cancelled`.

## Plattformhinweise
- Android: kleinere Profile bevorzugen (`wiki_small_android`), Speicherwarnungen beachten.
- Desktop/Server: `wiki_full_desktop` für reichere Relationen/Graph nutzbar.
- Container: Daten unter `data_dir/knowledge_indices/wiki` verwalten.

## Cleanup
- Alte Index-Läufe unter `knowledge_indices/wiki/<index-id>/<run-id>` gezielt entfernen.
- Bei Neuaufbau vollständigen Lauf mit frischem Output-Verzeichnis starten.

## Release-Gate
- Wiki-Smokes müssen dokumentieren, ob nur Fixture/Ausschnitt oder Voll-Dump geprüft wurde.
- CI-Workflow: `.github/workflows/android-delivery-apk.yml`.
- Der Workflow muss APK-Artefakt plus `wiki-release-verification-report.json` bereitstellen.
- Aktueller Gate-Standard: `full_dewiki_validated=false` ist erlaubt, wenn Fixture-/Ausschnitt-Smokes erfolgreich und explizit dokumentiert sind.
