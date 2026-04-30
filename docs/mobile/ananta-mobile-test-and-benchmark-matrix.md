# Ananta Mobile Test and Benchmark Matrix

Date: 2026-04-30
Scope: ANM-080..ANM-085

## Smoke-Test (ANM-080)

- Ziel: kleines GGUF laden und kurze Ausgabe erzeugen.
- Pfad:
  - Route `/llama-runtime`
  - Modell laden (`loadModel`)
  - kurzer Prompt (`generate`)
- Erwartung: Antworttext ohne Crash, Runtime-Status bleibt stabil.

## Speicherleck-Test (ANM-081)

- Ziel: wiederholtes Laden/Entladen ohne ansteigenden Restzustand.
- Schleife (manuell/skriptbar):
  - `loadModel` -> `generate` -> `unloadModel`
  - 30+ Wiederholungen
- Beobachtung:
  - kein dauerhafter Anstieg der Speicherbelegung
  - kein Deadlock im Plugin-Thread

## Abbruch-Test (ANM-082)

- Ziel: laufende Antwort sicher stoppen.
- Pfad:
  - `generate` starten
  - `stopGeneration` ausloesen
- Erwartung: keine App-Blockade, kontrollierter Ruecksprung in bereit-Zustand.

## Offline-Test (ANM-083)

- Ziel: lokale Ausfuehrung ohne Netz.
- Bedingungen:
  - Flugmodus aktiv
  - lokale Modell- und Runner-Dateien vorhanden
- Erwartung:
  - `verifySetup` und lokale Transkription funktionieren
  - Download-Aufrufe werden durch Netzpolicy erwartbar abgelehnt.

## Audio-Test (ANM-084)

- Ziel: Aufnahme + STT-Pipeline validieren.
- Pfad:
  - Mikrofon-Permission
  - Push-to-talk Aufnahme
  - `transcribe` mit lokalem Runner
- Erwartung: lesbares Transkript, Fehler als klare Meldung.

## Benchmark-Matrix (ANM-085)

Pro Geraet und Modell folgende Kennzahlen erfassen:

- Ladezeit Modell (s)
- mittlere Antwortlatenz (s)
- Tokens/s (Textmodus)
- Audio-Latenz pro Chunk (s)
- RAM beim Laden und unter Last (MB)
- Stabilitaet ueber 30 Wiederholungen (Pass/Fail + Fehlerzaehler)

Empfohlene Vergleichsachsen:

- Modelle: Qwen2.5 0.5B, Qwen2.5 1.5B, TinyLlama, Voxtral Mini
- Quantisierung: q4 vs q8
- Geraeteklasse: 6 GB vs 8 GB RAM
