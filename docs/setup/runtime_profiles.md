# Runtime Profile Empfehlungen

Die Empfehlungen sind konservative Startwerte und werden explizit als Profilwerte ausgegeben.

## Ziel

- reproduzierbare Defaults fuer unterschiedliche Hardware-/Runtime-Umgebungen
- klare Grenzen fuer Kontext, Token und Patch-Groesse
- **keine stille Auswahl von paid/cloud Providern** ohne explizite Konfiguration

## Empfohlene Profile

| Umgebung | Provider | Modell | Kontextfenster | Max Input | Max Output | RAG Budget | Patch-Groesse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu-only` | `ollama` | `qwen2.5-coder:7b` | 32k | 8k | 1024 | 12k | 120 Zeilen |
| `nvidia-gpu` | `ollama` | `qwen2.5-coder:14b` | 64k | 16k | 2048 | 32k | 220 Zeilen |
| `remote-model` (explizit konfiguriert) | `openai-compatible` | `model` | 64k | 24k | 2048 | 32k | 180 Zeilen |
| `mixed-local-remote` (explizit remote) | `openai-compatible` + lokal priorisiert | `model` | 64k | 20k | 2048 | 32k | 180 Zeilen |

Wenn `remote-model` oder `mixed-local-remote` ohne expliziten Remote-Endpoint angefragt werden, faellt die Empfehlung auf lokale sichere Defaults zurueck und markiert den Remote-Pfad als konfigurationspflichtig.

## Output im `ananta init` Profil

`ananta init` schreibt die Empfehlung in `runtime_recommendation` inklusive:

- `context_window_tokens`
- `max_input_tokens`
- `max_output_tokens`
- `rag_budget_tokens`
- `patch_size_lines`
- `requires_explicit_provider_config`

