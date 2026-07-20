# Speech model license review

Stand: 2026-07-19. Diese technische Prüfung ersetzt keine Rechtsberatung.

## Freigabestatus

| Komponente | Upstream-Bindung | behauptete Lizenz | Ananta-Status |
|---|---|---|---|
| OpenVoice V2 source | Commit `74a1d147b17a8c3092dd5430504bd83ef6c7eb23` | MIT laut Upstream-Repository | Quellcode-Kandidat |
| MeloTTS source | Commit `209145371cff8fc3bd60d7be902ea69cbdb7965a` | Upstream-Nachweis vor Packaging erneut prüfen | nicht paketiert |
| OpenVoice checkpoints | noch kein lokaler SHA-256-/Lizenzkatalog | nicht aus Quellcode-Lizenz ableiten | No-Go |
| XTTS-v2 weights | offizieller Modellstand | Coqui Public Model License, nicht-kommerziell | abgelehnt |
| StyleTTS2 dependency graph | kein vollständig gepinnter Bundle-Nachweis | gemischte Einzelkomponenten möglich | abgelehnt |
| Piper voices/models | kein einzelnes zugelassenes Voice-Bundle | modell-/datasetabhängig | abgelehnt |

## Pflichtnachweise vor Aktivierung von `openvoice_v2`

- Jeder Download wird außerhalb des Containers beschafft, auf SHA-256 geprüft
  und als read-only Hub-Artefakt zugelassen. Runtime-Downloads sind verboten.
- Die Lizenzakte umfasst Quellcode, Gewichte, Base-Speaker, VAD, Tokenizer,
  Vocoder, Phonemizer und die Herkunft der Trainingsdaten, soweit verfügbar.
- Export und Nutzung eines persönlichen Adapters benötigen getrennte aktive
  Einwilligungen. Widerruf und Ablauf entfernen Artefakt und lokalen Cache.
- Fremde Stimmen, nicht consentierte Sprecher und unklare Pair-Zuordnung
  werden bereits bei Admission abgewiesen.
- Ein SBOM-/License-Gate muss den gepinnten Container und die Modellmanifest-
  Digests gemeinsam prüfen.

Solange einer dieser Nachweise fehlt, darf weder das reale Backend in der
Worker-Registry erscheinen noch ein reales Image veröffentlicht werden.
