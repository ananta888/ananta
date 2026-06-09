# ML-Intern LoRA/QLoRA Fine-Tuning Pipeline

## Überblick

Dieses Dokument beschreibt die LoRA/QLoRA Fine-Tuning-Pipeline, die auf dem bestehenden
`ml_intern`-Spike aufbaut. **Ziel** ist nicht, Projektwissen in Modellgewichte zu pressen,
sondern kleine Adapter für Antwortstruktur, Todo-JSON-Qualität, Review-Stil,
Architekturdisziplin und Security-Policy-Verhalten reproduzierbar zu trainieren, evaluieren,
registrieren und optional zur Laufzeit zu routen.

---

## 1. Ist-Zustand des ml_intern-Spikes

### Vertrag `invoke_spike`

```python
invoke_spike(
    prompt: str,
    agent_cfg: dict | None,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict
```

Der Spike ist ein **bounded External-Worker-Executor**: Er führt einen externen Befehl
mit einem Prompt aus und gibt stdout/stderr zurück. Er ist kein Fine-Tuning-Mechanismus.

### Aktuelle Bounds

| Parameter | Min | Max | Default |
|-----------|-----|-----|---------|
| `timeout_seconds` | 10 s | 900 s | 180 s |
| `max_prompt_chars` | 512 | 64 000 | 6 000 |
| `max_output_chars` | 512 | 64 000 | 8 000 |
| `working_dir` | Innerhalb repo root | — | repo root |

- `env_allowlist`: Nur explizit erlaubte Umgebungsvariablen werden weitergegeben.
- Backend `ml_intern` ist nur aktiv, wenn `ml_intern_spike.enabled=true` in der Config.
- Der SGPT-Pfad akzeptiert **keine CLI-Flags** für `ml_intern` (returns 400).

### Was der Spike NICHT ist

> **Der bestehende ml_intern-Spike ist ein bounded Prompt-Execution-Mechanismus,
> kein Fine-Tuning-System.**

`/api/sgpt/execute` bleibt ausschließlich Prompt-Ausführung. Training-Jobs werden
über einen eigenen Service-Vertrag (`MlInternTrainingJobService`) verarbeitet und
sind **niemals** über den SGPT-Endpunkt erreichbar.

---

## 2. Architekturgrenze: SGPT-Execution vs. ML-Training

```
/api/sgpt/execute
    ├── ml_intern  → invoke_spike()  [bounded prompt execution]
    └── andere Backends           [CLI runner]

MlInternTrainingJobService.submit_job()
    ├── dataset_validate   [kein GPU]
    ├── train_lora         [GPU, nur wenn enabled+mode!=dry_run]
    ├── evaluate_lora      [GPU/CPU]
    ├── register_adapter   [kein GPU]
    ├── export_adapter     [kein GPU]
    └── merge_adapter_optional  [GPU, erfordert allow_merge=true]
```

**Invarianten:**

1. `MlInternTrainingJobService` ist niemals über `/api/sgpt/execute` erreichbar.
2. Training-Jobs sind default `enabled=false` und `mode=dry_run`.
3. Risikoreiche Job-Typen (`train_lora`, `merge_adapter_optional`) sind getrennt
   von ungefährlichen (`dataset_validate`).
4. Kein Adapter wird automatisch aktiviert — immer manuelles Approval nach Evaluation.

---

## 3. Verantwortlichkeiten: RAG/CodeCompass vs. LoRA

### CodeCompass/RAG — kanonischer Projektwissens-Layer

- Liefert **aktuelles Projektwissen** aus dem Repository (Code, Docs, Configs).
- Retrieval ist immer frisch aus dem Repo; kein Staleness-Problem.
- Zuständig für: Kontext, Quellenreferenzen, Code-Snippets, Dokumentation.

### LoRA/QLoRA — Verhalten/Stil/Struktur-Layer

- Verbessert **wiederkehrende Antwortformate**, Stil, Review-Verhalten, Pattern-Disziplin.
- Beispiel: Konsistentes Todo-JSON-Format, strukturierte Code-Review-Antworten.
- **NICHT zuständig für Projektwissen** — Repo-Facts werden nicht in Gewichte gebrannt.

> ⚠️ **Warnung:** Repo-spezifisches Wissen soll NICHT in LoRA-Adapter trainiert werden,
> weil es schnell stale wird. RAG bleibt die einzige Quelle für aktuelle Repo-Informationen.

### Empfohlener erster Adapter: `ananta-todo-json`

Ziel-Modell: `qwen2.5-coder-7b` (QLoRA)

Warum `ananta-todo-json` als erster Adapter:
- Todo-JSON-Qualität ist **automatisch testbar**: valides JSON, Track-Schema-Nähe,
  Milestones, Tasks, Acceptance Criteria, Test Expectations.
- Keine Repo-spezifischen Fakten nötig — nur Strukturdisziplin.
- Evaluations-Metriken sind deterministisch (JSON-Validität, Pflichtfelder).

---

## 4. Config-Shape

```json
{
  "ml_intern_training": {
    "enabled": false,
    "mode": "dry_run",
    "backend": "unsloth",
    "allowed_job_types": [
      "dataset_validate", "train_lora", "evaluate_lora",
      "register_adapter", "export_adapter", "merge_adapter_optional"
    ],
    "artifact_root": "artifacts/lora",
    "dataset_root": "data/training/lora",
    "timeout_seconds": 3600,
    "max_dataset_bytes": 104857600,
    "require_dataset_validation": true,
    "require_secret_scan": true,
    "require_eval_before_approval": true,
    "auto_activate_adapter": false,
    "gpu_profile": "rtx3080-safe",
    "external_network_allowed": false,
    "env_allowlist": ["HOME", "PATH", "CUDA_VISIBLE_DEVICES", "HF_HOME", "TRANSFORMERS_CACHE"]
  },
  "lora_runtime": {
    "enabled": false,
    "adapter_registry_path": "artifacts/lora/adapter_registry.json",
    "routing_enabled": false,
    "fallback_to_base_model": true,
    "approved_only": true
  }
}
```

---

## 5. GPU-Profil: `rtx3080-safe`

| Parameter | Safe Default | Hinweis |
|-----------|-------------|---------|
| `load_in_4bit` | `true` | QLoRA — VRAM-schonend |
| `lora_rank` | 16 | Erhöhen nur mit explizitem Override |
| `lora_alpha` | 32 | — |
| `lora_dropout` | 0.05 | — |
| `max_seq_length` | 2048 | 4096 nur mit Override + ausreichend VRAM |
| `batch_size` | 2 | — |
| `gradient_accumulation_steps` | 4 | Effektive Batch-Size = 8 |
| `learning_rate` | `2e-4` | — |

**Realistische Modell-Größen auf RTX-3080 (10 GB VRAM):**
- ✅ 7B/8B Modelle (z. B. `qwen2.5-coder-7b`) — realistisch
- ⚠️ 14B — knapp, nur mit aggressivem 4-bit Quantization
- ❌ 32B — nicht realistisch ohne Multi-GPU

---

## 6. Training-Pipeline (Schrittfolge)

```
1. Dataset erstellen (data/training/lora/)
2. dataset_validate  → dataset_validation_report.json
3. Secret-Scan       → in report enthalten, blockiert bei Findings
4. Dry-Run           → prüft Config/Pfade, kein GPU
5. train_lora        → adapter_model.safetensors + training_log.jsonl
6. evaluate_lora     → eval_report.json (base vs. adapter)
7. [manuelles Approval] → adapter_registry.json: status=approved
8. [optional] Routing aktivieren (lora_runtime.routing_enabled=true)
9. [optional] Rollback → base_model_only
```

---

## 7. Artefakt-Dateien

| Datei | Beschreibung |
|-------|-------------|
| `dataset_validation_report.json` | Counts, warnings, errors, hashes |
| `training_summary.json` | Job-ID, Modell, Methode, Hashes, Status |
| `training_log.jsonl` | Verlauf pro Schritt (loss, lr, step) |
| `adapter_config.json` | LoRA-Config (rank, alpha, target_modules) |
| `adapter_model.safetensors` | Adapter-Gewichte |
| `eval_report.json` | base_output vs. adapter_output, rule_scores |
| `adapter_registry.json` | Registry aller Adapter mit Status |

---

## 8. Sicherheitsregeln

| Risiko | Maßnahme |
|--------|----------|
| Training privater Daten | Secret-Scan blockiert per Default |
| Adapter ohne Evaluation | Approval-Gate: trained→approved nur nach Eval |
| Auto-Aktivierung | `auto_activate_adapter=false` ist unveränderlicher Default |
| Merge-Risiko | `merge_adapter_optional` erfordert `allow_merge=true` |
| VRAM-Überlauf | `gpu_profile=rtx3080-safe` mit konservativen Defaults |
| Pfad-Traversal | `artifact_root` wird validiert; kein `..` erlaubt |
| Externe Netzwerkzugriffe | `external_network_allowed=false` per Default |

---

## 9. Hermes/PEFT als optionale Vergleichsstrategie

Hermes (PEFT/LoRA-Orchestrator) kann als externer Vergleichspfad genutzt werden.

**Regeln:**
- Hermes ist **keine harte Dependency** im Ananta-Core.
- Hermes-Artefakte dürfen nur importiert werden, wenn sie dieselbe
  Adapter-Registry- und Eval-Pipeline durchlaufen haben.
- Ananta nutzt `ml_intern` als primäre eigene Integrationsgrenze.

---

## 10. Rollback

```bash
# Adapter auf deprecated setzen
# → lora_runtime Router fällt automatisch auf base_model_only zurück

# Oder: lora_runtime.routing_enabled=false setzen
# → kein Adapter-Routing, base_model_only für alle Tasks
```

---

## Offene Fragen

1. Soll der Training-Runner als Python-Modul im Repo liegen oder als externen
   `command_template`-artigen Prozess gestartet werden?
2. Soll es eine eigene API für Trainingsjobs geben oder nur Service-intern/CLI?
3. Adapter-Routing: in bestehende Model-Profile integriert oder als separater `lora_runtime` Layer?
4. Welche lokalen Basismodelle sind auf der RTX-3080 tatsächlich verfügbar?
5. Welche Todo-/Review-Beispiele dürfen als Trainingsdaten genutzt werden?
