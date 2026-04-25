# ananta init

`ananta init` erstellt einen **reviewbaren Runtime-Profile-File** und kann optional direkt einen passenden Config-Patch in `config.json` schreiben.

Der Wizard unterstuetzt:

- Runtime-Modus: `local-dev`, `sandbox`, `strict` (oder `auto` mit Erkennung)
- LLM-Backend: `ollama`, `lmstudio`, `openai-compatible`, `manual`
- Lokalen Start ohne Docker-Zwang im Modus `local-dev`

## Schnellstart

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
```

Das erzeugt standardmaessig:

- `ananta.runtime-profile.json` (reviewbare Setup-Datei)

## Mit direktem Config-Patch

```bash
ananta init --yes \
  --runtime-mode sandbox \
  --llm-backend openai-compatible \
  --endpoint-url http://127.0.0.1:1234 \
  --model qwen2.5-coder:7b \
  --apply-config
```

Dabei wird zusaetzlich `config.json` aktualisiert (u. a. `runtime_profile`, `governance_mode`, Backend-Defaults).

## Interaktiv

```bash
ananta init
```

Ohne `--yes` fragt der Wizard fehlende Werte interaktiv ab.

## Wichtige Optionen

- `--runtime-mode auto|local-dev|sandbox|strict`
- `--llm-backend ollama|lmstudio|openai-compatible|manual`
- `--endpoint-url <url>`
- `--model <model-id>`
- `--manual-json '{"default_provider":"custom","default_model":"model"}'`
- `--profile-path <path>`
- `--apply-config`
- `--config-path <path>`
- `--yes`
- `--force`

## Rueckgabecode

- `0`: erfolgreich
- `2`: Eingabe-/Validierungsfehler (z. B. ungueltige Werte oder vorhandene Zieldatei ohne `--force`)

