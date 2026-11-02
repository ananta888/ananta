# Repository-Struktur — Bounded Contexts (DDD-orientiert)

Ananta ist ein Hub-Worker-Orchestrator. Die Verzeichnisstruktur folgt der
Domain-Gliederung; sie ist pragmatisch, nicht dogmatisch — Python-Pakete
bleiben flach am Root (flache Import-Roots `agent.*`, `worker.*`, `hub.*`),
alles drumherum liegt in semantischen Homes.

## Bounded Contexts (Python-Pakete, Import-Roots)

| Pfad | Kontext | Rolle |
|---|---|---|
| `hub/` | **Hub** | Control Plane: Orchestrierung, Governance, Task-Queue |
| `agent/` | **Hub-Anwendung** | Routes/Services/Bootstrap des Hubs (Flask) |
| `worker/` | **Worker** | Execution-Engine, Adapters — fuehrt delegierte Arbeit aus |
| `ananta_contracts/` | **Shared Kernel** | Verträge/Datentypen zwischen Hub und Worker |
| `ananta_codecompass/` | **CodeCompass** | Code-Intelligenz-Context (Graph, Query) |
| `voice_runtime/` | **Voice** | Speech/Streaming-Runtime |
| `client_surfaces/`, `client_runtime/` | **Surfaces** | TUI-/Client-Laufzeiten |
| `frontend-angular/`, `web/` | **Web UI** | Angular-Frontend + statische Site |
| `domains/` | **Domain-Adapters** | Blender, FreeCAD, KiCad, Worker-Beispiele |
| `packages/` | **Spiele-Core** | ananta-game-core (npm-Kontext) |

## Services (Sidecar-Services, eigenständige Deployables)

| Pfad | Rolle |
|---|---|
| `services/sfu-runtime-agent/` | SFU-Runtime (eigenes pyproject) |
| `services/turn-observer-agent/` | TURN-Observer (eigenes pyproject) |
| `services/evolver-bridge/` | GEP-Evolver-Bridge (Node) |

## Infrastruktur / Ops

| Pfad | Inhalt |
|---|---|
| `docker/` | Dockerfiles, `compose-next/`, `compose/` (alle Stack-Compose-Dateien), `old_way/` (Legacy) |
| `deploy/` | Deploy-Assets: keycloak, examples, `setup/` (Windows-Bootstrap-Skripte) |
| `config/` | App-Konfiguration (`config.json`, `config.example.yaml`, `log_config.yaml`, profiles) |
| `migrations/` | Alembic |
| `schemas/` | JSON-Schemas (Contracts der Persistence/API) |
| `scripts/`, `devtools/` | Build-/Prüf-Skripte; `scripts/check_*.py` sind die Architektur-Detektoren |
| `reports/`, `artifacts/`, `ci-artifacts/` | Gate-Berichte/Evidenz (siehe AGENTS.md Output Boundary) |

## Wissen / Planung

| Pfad | Inhalt |
|---|---|
| `docs/` | Gesamte Doku inkl. `architecture/`, lose Root-MDs liegen hier |
| `todos/` | Track-basierte Planungs-Artefakte |
| `examples/` | Beispiel-/Referenz-Implementierungen (`minimal-hub/`, fairphone) |
| `sources/` | Referenz-Quellmaterial (Blender/FreeCAD/Eclipse/Keycloak/…) |
| `heuristics/`, `simulation/`, `benchmarks/`, `experiments/` | Forschung/Simulation |

## Root-Verbleib (Konvention)

`README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `LICENSE*`, `CLA.md`,
`CHANGELOG.md`, `THIRD_PARTY_NOTICES`, `Makefile`, `pyproject.toml`,
`requirements*.txt/.lock`, `alembic.ini`, `.env.example`, `.envrc` —
Packaging-/Repo-Konvention, bleibt am Root.

## Migrationsnotiz

Die Neusortierung (2026-11) verschob lose Root-Dateien in semantische Homes
(`docker/compose/`, `config/`, `docs/`, `deploy/setup/`). Alle Code-,
Skript- und CI-Referenzen wurden mitgezogen; die Validierung erfolgt ueber
die Standard-Gates (`make check`, `make test_full_docker`).
