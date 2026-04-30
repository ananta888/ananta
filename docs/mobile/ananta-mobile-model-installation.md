# Ananta Mobile Model Installation Guide

Date: 2026-04-30
Scope: ANM-091

## Voraussetzungen

- Native Android-App gestartet
- Route `/voxtral-offline`
- Mikrofon-Permission erteilbar

## Modellinstallation (in App)

1. Modell-Preset auswaehlen oder `Model-URL` eintragen.
2. `Model laden` ausfuehren.
3. Ergebnispfad uebernehmen (`modelPath`).

## Runner-Installation (in App)

1. `Runner-Preset (auto)` waehlen oder `Runner-URL` eintragen.
2. `Runner laden` ausfuehren.
3. Ergebnispfad uebernehmen (`runnerPath`).

## Setup-Pruefung

1. `Setup pruefen` ausfuehren.
2. Erfolgsfall erwartet:
   - `modelExists=true`
   - `modelCompatible=true` (`.gguf`)
   - `runnerExecutable=true`
   - `runnerCompatible=true`
   - `hasEnoughStorage=true`

## Sicherheitshinweise

- Download ist auf HTTPS und vertrauenswuerdige Host-Suffixe begrenzt.
- Riskante Aktionen benoetigen explizite Bestaetigung (`confirmed=true`).
