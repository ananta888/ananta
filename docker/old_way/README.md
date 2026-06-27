# Legacy Docker Compose

Dieser Ordner enthält die frühere, mehrschichtige Compose-Konfiguration.
Sie bleibt für bestehende Spezial-, CI- und E2E-Abläufe verfügbar, ist aber
nicht mehr der Standardpfad für lokale Entwicklung oder neue Deployments.

Der aktive Compose-Pfad liegt unter [`../compose-next`](../compose-next/README.md).

Legacy-Kommandos werden vom Repository-Root mit expliziten Pfaden ausgeführt:

```bash
docker compose --env-file .env \
  -f docker/old_way/docker-compose.base.yml \
  -f docker/old_way/docker-compose-lite.yml \
  up -d --build
```

Relative Build-Kontexte und Includes in diesen Dateien sind auf ihren Standort
unter `docker/old_way/` abgestimmt. Dateien dürfen nicht zurück ins
Repository-Root kopiert werden.

Das explizite `--env-file .env` ist nötig, weil Compose seine automatische
Umgebungsdatei sonst relativ zur ersten Compose-Datei unter `docker/old_way/`
suchen kann.
