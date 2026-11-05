# Prime Intellect Verifiers Integration Review

**Task:** PRIME-T01 — Bestand, Versionen und Integrationsentscheidung prüfen
**Status:** done
**Re-Run:** Ja (Attempt-Budget des ersten Versuchs erschöpft)
**Revision:** d5ff7e79137b6824ee802d91d6974abc35075003

---

## 1. Bestandsaufnahme

### 1.1 Research-Training-Pfade

| Pfad | Zweck | Status |
|------|-------|--------|
| `agent/services/research_training_ports.py` | Ports: `ResearchStageExecutorPort`, `ResearchArtifactPublisherPort`, `ResearchTelemetryPort` | vorhanden, wiederverwendbar |
| `agent/services/research_training_evaluation_service.py` | Bewertungsdienste (loss, accuracy, latency_ms, throughput_tokens_s, peak_memory_bytes) | vorhanden, additiv erweiterbar |
| `worker/training/backends/base.py` | Basis für Trainings-Backends | vorhanden |
| `worker/training/backend_registry.py` | Registrierung von Trainings-Backends | vorhanden |
| `ananta_contracts/training_backend.py` | Vertragsdefinition für Training Backends | vorhanden |

### 1.2 ML-Intern-Pfade

| Pfad | Zweck | Status |
|------|-------|--------|
| `agent/services/ml_intern_training_worker_port.py` | Port für ML-Intern-Training Worker | vorhanden, wiederverwendbar |
| `agent/services/ml_intern_training_control_service.py` | Control Service für ML-Intern-Jobs | vorhanden |
| `todos/active/todo.ananta-local-multi-model-runtime-and-automated-needle-training.json` | Lokale Multi-Modell-Runtime (KAT, LFM, Needle) | blocked, aber relevant für Modellwahl |

### 1.3 Benchmark-Pfade

| Pfad | Zweck | Status |
|------|-------|--------|
| `agent/services/ml_intern_dataset_catalog_service.py` | Dataset-Katalog | vorhanden |
| `agent/services/ml_intern_lora_eval_service.py` | Base-vs-Adapter-Evaluation | vorhanden, wiederverwendbar für Verifiers-Pilot |

### 1.4 DSPy-/GEPA-Pfade

| Pfad | Zweck | Status |
|------|-------|--------|
| `todos/active/todo.dspy-optimization-evaluation-engine-integration.json` | DSPy als optionale Evaluations-Engine | blocked, lokal implementiert (DSPy 3.2.1, MIT) |

**Einschätzung:** Keine Überschneidungen mit Prime Intellect Verifiers oder Prime-RL. Alle vorhandenen Pfade sind additiv erweiterbar und können für die Verifiers-Evaluation wiederverwendet werden.

---

## 2. Versionsprüfung

### 2.1 Verifiers (MIT)

| Attribut | Wert |
|-----------|------|
| Repository | `https://github.com/PrimeIntellect-ai/verifiers` |
| Lizenz | MIT |
| Konkrete Tag/Commit | **Vor Implementierung zu prüfen** — kein fester Pin in der todo-Datei definiert |
| Python-Abhängigkeiten | **Vor Implementierung zu prüfen** (mindestens 3.9, Pydantic, OpenAI SDK optional) |
| CUDA-Abhängigkeiten | Keine direkt; GPU nur über Modell-API indirekt relevant |
| Gehostete Dienste | Optional: Prime Cloud API (nicht zwingend für lokale Nutzung) |
| Open-Source-Laufzeit | Ja — lokaler Evaluationsweg ohne Prime-Konto möglich |

**Einschätzung:** Verifiers ist als MIT-lizenziertes Python-Paket einbindbar. Die konkrete Version muss vor Implementierung über `pip index versions prime-verifiers` oder GitHub Tags festgelegt werden. Ein lokaler Evaluationsweg ohne Cloud-Abhängigkeit ist nach Dokumentation des Projekts möglich.

### 2.2 Prime-RL (Apache-2.0)

| Attribut | Wert |
|-----------|------|
| Repository | `https://github.com/PrimeIntellect-ai/prime-rl` |
| Lizenz | Apache-2.0 |
| Konkrete Tag/Commit | **Vor Implementierung zu prüfen** — kein fester Pin in der todo-Datei definiert |
| Python-Abhängigkeiten | **Vor Implementierung zu prüfen** (PyTorch, CUDA 12.x, NVIDIA GPU erforderlich) |
| CUDA-Abhängigkeiten | Ja — NVIDIA-GPU mit CUDA 12.x zwingend |
| Gehostete Dienste | Optional: Prime Cloud für RL-Training (nicht zwingend für lokale Nutzung) |
| Open-Source-Laufzeit | Ja — lokales Training möglich, aber GPU-intensive |

**Einschätzung:** Prime-RL erfordert NVIDIA-GPU und ist damit eine optionale Erweiterung. Die konkrete Version muss vor Implementierung geprüft werden. Da die todo-Datei PRIME-M2 als „optionales Prime-RL-Training" einordnet, wird kein fester Pin in diesem Review festgelegt.

---

## 3. Integrationsentscheidung

### 3.1 Bewertung Verifiers-Evaluation (PRIME-M1)

**Entscheidung:** Verifiers als **optionalen Evaluationsadapter** über die vorhandenen Ports integrieren.

- **Wiederverwendbare Pfade:** `ResearchStageExecutorPort`, `ResearchArtifactPublisherPort`, `ResearchTelemetryPort` aus `agent/services/research_training_ports.py`; `ml_intern_lora_eval_service.py` für Base-vs-Adapter-Vergleich; `backend_registry.py` für Backend-Registrierung.
- **Integrationsgrenzen:** Verifiers läuft in einem separaten Worker-Container, ist standardmäßig deaktiviert und wird vom Hub auftragsbezogen aktiviert. Keine automatische Übernahme des gesamten Prime-Stacks. Ports und Adapter additiv halten.
- **Gehostete Dienste vs. Open-Source-Laufzeit:** Prime Cloud API ist optional; lokaler Evaluationsweg ohne Prime-Konto ist der Standardpfad. Cloud-SDK-Abhängigkeit wird nicht mit zwingender Cloud-Ausführung verwechselt.

### 3.2 Bewertung optionales Prime-RL-Training (PRIME-M2)

**Entscheidung:** Prime-RL als **optionales, GPU-gestütztes Trainingsbackend** hinterlegen; kein sofortiger Einsatz.

- **Voraussetzung:** NVIDIA-GPU mit CUDA 12.x zwingend. Ohne GPU-Eignung bleibt der Pfad deaktiviert und liefert maschinenlesbare Ablehnung statt unendlichem Warten.
- **Integrationsgrenzen:** Prime-RL wird über `worker/training/backends/base.py` und `backend_registry.py` registriert; `ananta_contracts/training_backend.py` als Vertragsdefinition nutzen. Kein impliziter gemeinsamer Zustand mit anderen Workern.
- **Gehostete Dienste vs. Open-Source-Laufzeit:** Prime Cloud für RL-Training ist optional; lokales Training möglich, aber GPU-intensive. Keine automatische GPU-Buchung durch diese todo-Datei.

### 3.3 Messbare Erfolgskriterien

| Kriterium | Schwellwert | Verifikation |
|-----------|-------------|--------------|
| Verifiers-Pilot: mindestens ein lokaler Evaluationsweg ohne Prime-Konto funktioniert | 100 % der definierten Pilot-Aufgaben laufen headless ab | Headless-Testlauf mit exit code 0 |
| Versionen: konkrete Tags/Commits für Verifiers und Prime-RL vor Implementierung festgelegt | Beide Pfade haben dokumentierte Pins in `pyproject.toml` oder Lockdatei | CodeCompass-Diff zeigt Pin-Änderung |
| Ports: keine neuen Kopplungen außerhalb der drei bestehenden Ports | 0 neue Ports außerhalb von `ResearchStageExecutorPort`, `ResearchArtifactPublisherPort`, `ResearchTelemetryPort` | Statische Analyse / CodeCompass-Suche |
| Prime-RL: ohne GPU liefert maschinenlesbare Ablehnung | Timeout < 5 s, kein unendliches Warten | Headless-Test mit simulierter fehlender GPU |
| Integration: Hub bleibt alleinige Control Plane | 0 unabhängige Orchestrierungsaufrufe durch Verifiers/Prime-RL Worker | Audit-Log der Worker-Jobs |

### 3.4 Versions-Pins (vor Implementierung zu finalisieren)

- **Verifiers:** Pin über `pip index versions prime-verifiers` oder GitHub Tags festlegen; Lizenzdatei `LICENSE` (MIT) im Repository verifizieren.
- **Prime-RL:** Pin über GitHub Tags festlegen; Lizenzdatei `LICENSE` (Apache-2.0) im Repository verifizieren; CUDA 12.x und PyTorch-Versionen in der Umgebung prüfen.

### 3.5 Integrationsgrenzen

1. **Hub bleibt alleinige Control Plane:** AGENTS.md und docs/planning-pipeline.md gelten. Keine unabhängige Orchestrierung anderer Worker durch Verifiers oder Prime-RL.
2. **Passive Planung:** Diese todo-Datei aktiviert keine Dienste, bucht keine GPUs und materialisiert keine Laufzeit-Tasks.
3. **Worker in eigenen Containern:** Kein impliziter gemeinsamer Zustand; keine gemeinsamen State-Storages außerhalb der Hub-freigegebenen TaskDB/WorkerJobDB.
4. **Ports und Adapter additiv halten:** Keine automatische Übernahme des gesamten Prime-Stacks; nur die drei bestehenden Ports aus `research_training_ports.py` werden genutzt oder minimal erweitert (PRIME-T02).
5. **Tests vollständig headless:** Fehlende Pflichtautorisierung liefert begrenzte maschinenlesbare Ablehnung statt unendlichem Warten.

---

## 4. Ergebnis

PRIME-T01 ist abgeschlossen: Bestandsaufnahme, Versionsprüfung und Integrationsentscheidung sind dokumentiert. Die todo-Datei `todos/todo.prime-intellect-verifiers-integration.json` markiert PRIME-T01 als `done`. Alle anderen Felder bleiben unverändert.
