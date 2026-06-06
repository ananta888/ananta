# Model Routing — Zielarchitektur: Profiles, Policy und Resolver (AMR-002)

## Kernprinzipien

- **Policy-first**: Sicherheitsklasse und Kontext-Sensitivität werden vor der Modellwahl geprüft
- **Deterministisch**: Keine implizite Heuristik als Default — jede Entscheidung hat einen dokumentierten Grund
- **Least Privilege**: Cloud-Provider, Tools und Kontext-Zugriff werden explizit erlaubt
- **Abwärtskompatibel**: `DEFAULT_PROVIDER`/`DEFAULT_MODEL` bleiben als unterster Fallback lauffähig
- **Nachvollziehbar**: Jede Routing-Entscheidung erzeugt `reason`, `policy_decisions`, `blocked_candidates`

## Konzepte

### Model Profile
Beschreibt **einen** Provider+Modell-Kombination mit Capabilities, Limits und Security-Flags:

```json
{
  "profile_id": "ollama-qwen-7b-local",
  "provider_id": "ollama",
  "model": "qwen2.5-coder:7b",
  "local": true,
  "cloud": false,
  "cloud_allowed": false,
  "block_secret_context": false,
  "supports_tools": false,
  "supports_json": true,
  "supports_streaming": true,
  "context_tokens": 32768,
  "cost_class": "free",
  "quality_class": "medium"
}
```

### Model Role
Fachliche Bezeichnung für eine Aufgabe, unabhängig von konkretem Provider:
`planner`, `coder`, `reviewer`, `embedder`, `summarizer`, `chat`

### Routing Rule
Verknüpft eine Model Role mit einem oder mehreren Model Profiles über Bedingungen:
`blueprint_id`, `task_kind`, `template_id`, `risk_class`, `team_id`

### Security Policy
Überschreibt jede andere Routing-Entscheidung:
- Cloud-Provider werden blockiert wenn `block_secret_context=true` und Secrets im Kontext
- OpenAI-kompatible externe Provider nur wenn explizit erlaubt

## Deterministische Resolver-Reihenfolge

```mermaid
flowchart TD
    A[Routing Request] --> B{Request-Level Override?}
    B -- ja --> Z[Profil aus Request]
    B -- nein --> C{Task-Level Override?}
    C -- ja --> Z
    C -- nein --> D{Blueprint Role Override?}
    D -- ja --> Z
    D -- nein --> E{Template Override?}
    E -- ja --> Z
    E -- nein --> F{Task-Kind Override?}
    F -- ja --> Z
    F -- nein --> G{Risk/Security Policy?}
    G -- blockiert --> BLOCKED[Blocked: policy_decision logged]
    G -- erlaubt --> H{Benchmark/Learning Preference?}
    H -- vorhanden --> Z
    H -- nein --> I[Global Default: DEFAULT_PROVIDER/DEFAULT_MODEL]
    I --> Z
    Z --> SEC{Security Check}
    SEC -- Secrets + cloud_not_allowed --> BLOCKED
    SEC -- OK --> RESULT[Resolved Profile + reason + telemetry]
```

## Precedence-Ränge (MPM)

| Rang | Typ | Quelle |
|------|-----|--------|
| 0 | security_policy | Immer — blockiert wenn nötig |
| 1 | request_override | Expliziter Caller-Override |
| 2 | task_override | Task-spezifisches Profil |
| 3 | blueprint_role_override | Blueprint-definierte Rolle |
| 4 | team_override | Team/Projekt-Override |
| 5 | template_override | Template-spezifisch |
| 6 | task_kind_override | Routing nach Task-Art |
| 7 | risk_class_policy | Risikoklassen-basiertes Routing |
| 8 | benchmark_preference | Gelerntes Modell-Ranking |
| 9 | planning_profile | Planning-Model-Profile-Match |
| 10 | global_default | DEFAULT_PROVIDER/DEFAULT_MODEL |
| 11 | legacy_fallback | Letzter Ausweg — immer lokal |

## Provider-Beispiele

### Lokal (Ollama)
```yaml
provider_id: ollama
base_url: http://localhost:11434
cloud: false
cloud_allowed: false
api_key_env: null
```

### Lokal (LM Studio)
```yaml
provider_id: lmstudio
base_url: http://localhost:1234
cloud: false
cloud_allowed: false
```

### Cloud (OpenRouter)
```yaml
provider_id: openrouter
base_url: https://openrouter.ai/api/v1
cloud: true
cloud_allowed: true            # muss explizit gesetzt sein
block_secret_context: true     # verhindert Secret-Leaks in Cloud-Calls
api_key_env: OPENROUTER_API_KEY
```

### Cloud (OpenAI)
```yaml
provider_id: openai
base_url: https://api.openai.com/v1
cloud: true
cloud_allowed: true
block_secret_context: true
api_key_env: OPENAI_API_KEY
```

## Neue Dateien (AMR-003–AMR-009)

| Datei | Zweck |
|-------|-------|
| `config/schemas/model_profiles.schema.json` | JSON-Schema für Profiles |
| `config/schemas/model_routing.schema.json` | JSON-Schema für Routing-Regeln |
| `config/models/examples/*.yaml` | Beispiele für Ollama, hybrid, cloud |
| `agent/services/model_profile_loader.py` | Lädt und validiert Profile |
| `agent/services/model_profile_resolver.py` | Deterministischer Resolver |
| `agent/services/model_override_normalization_service.py` | Legacy-Normalisierung |
