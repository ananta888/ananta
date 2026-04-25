# Deployment Targets (optional)

`ananta init` kann optional Deployment-Profile fuer `docker-compose` oder `podman` erzeugen.

## Grundsatz

- `local-dev` bleibt standardmaessig **non-container**
- `sandbox` und `strict` werden als **staerkere Isolation** markiert
- bestehende Deployment-Dateien werden **nicht still ueberschrieben**

## Beispiele

```bash
ananta init --yes \
  --runtime-mode sandbox \
  --llm-backend ollama \
  --deployment-target docker-compose
```

```bash
ananta init --yes \
  --runtime-mode strict \
  --llm-backend lmstudio \
  --deployment-target podman \
  --apply-config
```

## Overwrite-Verhalten

Wenn die Ziel-Datei bereits existiert:

1. mit `--force`: explizite Ueberschreibung
2. ohne `--force` und mit `--backup-existing-deployment` (Default): Backup-Datei wird erstellt
3. ohne beides: Fehler statt stiller Ueberschreibung

