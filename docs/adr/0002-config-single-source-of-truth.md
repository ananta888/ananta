# ADR-0002: Konfiguration — eine Quelle, dokumentierte Präzedenz (CLN-062)

## Status

Akzeptiert (2026-11-01). Basiert auf der Cleanup-Analyse: ≥7 konkurrierende
Config-Mechanismen mit impliziter Präzedenz (1.009 Env-Namen in 2 Binding-Stilen,
233 config/policy-Services, Root-Runtime-State, DB-Config-Graph).

## Kontext

Ananta liest Konfiguration heute aus (Reihenfolge der Historie, nicht der
Gültigkeit):

1. Prozess-Environment (`os.environ` / `os.getenv`, ~723 Namen im Code)
2. `pydantic-settings` (`agent/config.py`, ~282 Felder, `validation_alias`)
3. `config_defaults*.py` (2.264 Zeilen Mega-Dict, Code-Kopien von Env)
4. `config/`-JSON/-YAML (config.json, config/cli_backends, hub_benchmark_config)
5. DB-Config-Table + Config-Graph-Stack (zur Laufzeit editierbar)
6. Profile-/Policy-Stack (`agent/services/*policy*`, ~100 Singletons)
7. Root-Runtime-State (user.json, ananta.runtime-profile.json — jetzt
   gitignore'd, M2)

Ohne dokumentierte Präzedenz gewinnt still, wer zuletzt schreibt; Doppelung
erzeugt Drift (die 50 KB `.env.example` drifteten um 242 Variablen).

## Entscheidung

**Kanonische Lesereihenfolge (höchste zuerst):**

1. **Explicite API-/Request-Overrides** (goal execution_preferences,
   config_snapshot — höchste, weil request-scoped)
2. **Prozess-Environment** (Betrieb/Docker injiziert; SECRET-Handling)
3. **`agent/config.py` (pydantic-settings)** — der einzige typisierte
   Settings-Ordnernamen; `validation_alias` deckt Alt-Env-Namen ab
4. **DB-Config-Graph / Config-Tabelle** (laufzeit-editierbar, verwaltet)
5. **`config/`-Statische Dateien** (JSON/YAML: objekt-/werkzeug-spezifisch)
6. **`config_defaults*.py`** — nur noch interner Residual, abzubauen
7. **Profil-/Policy-Services** — Domänenentscheider, lesen 1–6, überschreiben
   nichts

**Schreibquelle:** nur (1) und (5) sind menschlich; (2) via Deployment; der
Rest ist ableitend. Keine zweite Schreibquelle.

## Konsequenzen (Deprecation-Pfad)

- `config_defaults*.py`: Einfrieren; neue Defaults kommen als pydantic-Felder
  oder config/-Dateien. Bestehende Dicts migrieren Modul-für-Modul; danach
  Löschen. Detektor: keine neuen Keys.
- Env-Referenz wird generiert (CLN-065: aus os.getenv-Sweep + validation_alias)
  statt 50 KB `.env.example` handzuhalten.
- Policy-Services (CLN-069, P3): konsolidieren hinter einem `PolicyPort`
  (docs/service-singletons.md-Muster); die Präzedenz oben beschreibt ihre
  Eingangsquellen, ersetzt sie nicht.
- Root-Runtime-State (user.json, *.runtime-profile.json) bleibt Runtime
  (Output Boundary) und zählt nicht als Config-Quelle.

## Geprüfte Alternativen

- Alles auf Env reduzieren: verwirft laufzeit-editierbare DB-Config (Anforderung).
- Alles in DB: verwirft Container-reproduzierbare Builds (12-Factor-Bruch).
- Status quo: erzeugt den dokumentierten Drift.
