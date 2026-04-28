# ananta init

`ananta init` erstellt einen **reviewbaren Runtime-Profile-File** und kann optional direkt einen passenden Config-Patch in `config.json` schreiben.

Der Wizard unterstuetzt:

- Runtime-Modus: `local-dev`, `sandbox`, `strict` (oder `auto` mit Erkennung)
- LLM-Backend: `ollama`, `lmstudio`, `openai-compatible`, `manual`
- Hardware-Profil fuer konservative Empfehlungen: `cpu-only`, `nvidia-gpu`, `remote-model`, `mixed-local-remote`
- Lokalen Start ohne Docker-Zwang im Modus `local-dev`
- Optionale Deployment-Profile fuer `docker-compose` oder `podman`

Wichtig: Dieses `--llm-backend` steuert den Inferenz-Provider. Das lokale CLI-Ausfuehrungsbackend (z. B. `ananta-worker` oder `opencode`) wird separat ueber `SGPT_EXECUTION_BACKEND` gesteuert.

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
  --endpoint-url http://127.0.0.1:1234/v1 \
  --model qwen2.5-coder:7b \
  --apply-config
```

Dabei wird zusaetzlich `config.json` aktualisiert (u. a. `runtime_profile`, `governance_mode`, Backend-Defaults).

## Mit optionalem Deployment-Profil

```bash
ananta init --yes \
  --runtime-mode sandbox \
  --llm-backend ollama \
  --deployment-target docker-compose
```

## Weiterfuehrende Doku

- Runtime-Empfehlungen: `docs/setup/runtime_profiles.md`
- Deployment-Targets: `docs/setup/deployment_targets.md`

## Interaktiv

```bash
ananta init
```

Ohne `--yes` fragt der Wizard fehlende Werte interaktiv ab.

## Wichtige Optionen

- `--runtime-mode auto|local-dev|sandbox|strict`
- `--llm-backend ollama|lmstudio|openai-compatible|manual`
- `--hardware-profile cpu-only|nvidia-gpu|remote-model|mixed-local-remote`
- `--endpoint-url <url>`
- `--model <model-id>`
- `--manual-json '{"default_provider":"custom","default_model":"model"}'`
- `--profile-path <path>`
- `--apply-config`
- `--config-path <path>`
- `--deployment-target none|docker-compose|podman`
- `--deployment-path <path>`
- `--[no-]backup-existing-deployment`
- `--yes`
- `--force`

## Migrationshinweis fuer URL-Flag

- Der aktuelle Init-Vertrag nutzt `--endpoint-url`.
- Falls alte interne Notizen/Snippets noch `--base-url` zeigen, bitte auf `--endpoint-url` migrieren.

## Laufzeit-Baseline

- Fuer den lokalen CLI-Pfad gilt aktuell Python `3.10+`.

## Rueckgabecode

- `0`: erfolgreich
- `2`: Eingabe-/Validierungsfehler (z. B. ungueltige Werte oder vorhandene Zieldatei ohne `--force`)
