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

**Einschätzung:** Prime-RL erfordert NVIDIA-GPU und ist damit eine optionale Erweiterung. Die konkrete Version muss vor Implementierung geprüft werden. Da die todo-Datei PRIME-M2 als „optionales Prime-RL-Training