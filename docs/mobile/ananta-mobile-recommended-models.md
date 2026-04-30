# Ananta Mobile Recommended Models

Date: 2026-04-30
Scope: ANM-093

## Primare Kandidaten

| Modell | Zweck | RAM-Klasse | Prioritaet |
|---|---|---|---|
| Qwen2.5 0.5B (GGUF q8) | schnelle lokale Smoke-Tests | 6 GB | hoch |
| Qwen2.5 1.5B (GGUF q4) | leichter Assistent | 8 GB | hoch |
| TinyLlama (GGUF q4) | stabile Baseline / Vergleich | 6 GB | mittel |
| Voxtral Mini 4B (GGUF q4) | Offline-STT/Audio | 8 GB | hoch |

## Auswahlregeln

- Text: zuerst 0.5B/1.5B fuer Latenz und Stabilitaet.
- Audio/STT: Voxtral Mini nur mit kompatiblem Runner.
- Bei knappen Ressourcen q4-Varianten bevorzugen.

## Nicht-Empfehlungen fuer Mobile-Default

- grosse 7B+ Modelle als Standardpfad
- multimodale mmproj-Varianten ohne klaren Use-Case
- Modelle ohne reproduzierbare Offline-Ergebnisse
