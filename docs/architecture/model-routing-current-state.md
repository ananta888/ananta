# Model Routing — Ist-Zustand (AMR-001)

> Bestandsaufnahme: Alle aktuellen Eingriffspunkte für Modell- und Provider-Wahl in Ananta.

## Eingriffspunkte

### 1. Globale Defaults (`agent/config.py`)

| Setting | Env-Variable | Default | Verwendung |
|---------|-------------|---------|------------|
| `default_provider` | `DEFAULT_PROVIDER` | `lmstudio` | Globaler Provider-Fallback |
| `default_model` | `DEFAULT_MODEL` | `auto` | Globales Modell-Fallback |
| `sgpt_default_model` | `SGPT_DEFAULT_MODEL` | `gpt-4o` | SGPT-Backend |
| `codex_default_model` | `CODEX_DEFAULT_MODEL` | `gpt-5-codex` | OpenCode Codex |
| `opencode_default_model` | `OPENCODE_DEFAULT_MODEL` | `opencode/glm-5-free` | OpenCode Standard |
| `aider_default_model` | `AIDER_DEFAULT_MODEL` | `None` | Aider Integration |
| `mistral_code_default_model` | `MISTRAL_CODE_DEFAULT_MODEL` | `None` | Mistral Code |
| `ollama_model` | `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Ollama Standard |

### 2. Planning Model Profiles (`config/planning_model_profiles.default.json`)

Existiert bereits als Runtime-Config. Enthält Provider-spezifische Profiles mit:
- `provider`, `model_name_pattern`, `profile_name`
- `context_max_chars`, `max_output_tokens`, `temperature`
- `repair_attempts`, `retry_attempts`, `output_contract_strictness`
- `requires_english_prompt`, `enabled`

**Status:** Runtime-konfigurierbar, aber kein formales JSON-Schema vorhanden. Profile sind hardcoded nach Providernamen ohne Capabilities- oder Security-Felder.

### 3. Worker Runtime Config (`config/workers/`, `agent/config.py`)

Worker-spezifische Modellwahl via `worker_runtime.*` Settings. Semantic Output Correction hat eigene embedding_provider-Config unter `worker_runtime.semantic_output_correction`.

### 4. Blueprint / Template / Task-Kind Overrides

- Blueprint-Definitionen können model-Rollen referenzieren
- Template-Level und Task-Kind-Overrides existieren konzeptuell, aber noch nicht als durchgehend validierter Config-Pfad
- Aktuell: Implizite Heuristik durch `planning_model_profiles.default.json` Pattern-Matching

### 5. URL-Konfiguration (runtime-konfigurierbar)

| Setting | Env-Variable | Default |
|---------|-------------|---------|
| `ollama_url` | `OLLAMA_URL` | `http://localhost:11434` |
| `lmstudio_url` | `LMSTUDIO_URL` | `http://localhost:1234` |
| `openai_url` | `OPENAI_URL` | `https://api.openai.com/v1` |
| `openrouter_url` | `OPENROUTER_URL` | `https://openrouter.ai/api/v1` |

### 6. Lücken (identifiziert für AMR)

- **Kein Capabilities-Vertrag**: Profile haben keine `supports_tools`, `supports_json`, `cloud_allowed`, `block_secret_context`-Felder
- **Kein Security-Layer**: Kein Mechanismus der einen Provider blockiert, wenn Secrets im Kontext sind
- **Keine Routing-Begründung**: Welches Modell warum gewählt wurde ist nicht nachvollziehbar
- **Keine Fallback-Kette**: Bei nicht verfügbarem Provider gibt es kein dokumentiertes Fallback-Schema
- **Kein Precedence-Vertrag**: Konflikt zwischen request-override, blueprint-override und global-default ist nicht definiert
