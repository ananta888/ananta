# Ananta Release-Prozess

Dieser Dokument beschreibt den standardisierten Release-Prozess für das Ananta-Projekt.

## 1. Voraussetzungen (Release Gate)

Bevor ein Release erstellt werden kann, müssen alle Qualitätsprüfungen bestanden sein.
Dazu gehört der `release-gate` Check, der sicherstellt, dass alle notwendigen Dateien vorhanden und die Abhängigkeiten konsistent sind.

```bash
make release-gate
```

## 2. Standard-Qualitätschecks

Führen Sie die Standard-Pipeline aus, um sicherzustellen, dass der Code den Qualitätsrichtlinien entspricht.

```bash
make check
```

Für eine vollständige Prüfung vor einem Major-Release:

```bash
make check-deep
```

## 3. Docker-Build und Validierung

Das Hauptartefakt von Ananta ist das Docker-Image.

### Build

```bash
docker build -t ananta:latest .
```

### Validierung des Images (Smoke Test)

Nach dem Build sollte das Image kurz gestartet werden, um sicherzustellen, dass die Abhängigkeiten korrekt geladen werden und der Agent-Core startfähig ist.

```bash
docker run --rm ananta:latest python -m agent.ai_agent --version
```
(Hinweis: Stellen Sie sicher, dass ein `--version` Flag oder ein ähnlicher Smoke-Befehl implementiert ist.)

## 4. Versionierung

Die Version wird in der `pyproject.toml` gepflegt.
Bei einem Release sollte diese Version inkrementiert werden.

## 5. CI/CD Integration

In der GitHub Actions Pipeline werden diese Schritte automatisch bei Pushes auf den `main` Branch oder bei Erstellung von Tags ausgeführt.
Ein Scheitern des `release-gate` blockiert den Build-Prozess.
