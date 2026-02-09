# Observability in Ananta

Ananta bietet ein integriertes Monitoring- und Logging-System basierend auf dem LGPP-Stack (Loki, Grafana, Promtail, Prometheus).

## Architektur

- **Prometheus**: Sammelt Metriken von den Agenten (Endpunkt `/metrics`).
- **Loki**: Zentraler Log-Aggregator.
- **Promtail**: Sammelt Logs vom Host und von Docker-Containern und sendet sie an Loki.
- **Grafana**: Visualisiert Metriken und Logs in Dashboards.

## Aktivierung

Die Observability-Komponenten sind über ein Docker Compose Profil geschützt und werden nicht standardmäßig gestartet.

Zum Starten mit Monitoring:
```bash
docker compose --profile observability up -d
```

## Zugriff

- **Grafana**: [http://localhost:3000](http://localhost:3000)
  - **Standard-Benutzer**: `admin`
  - **Standard-Passwort**: `admin_change_me` (konfigurierbar via `GRAFANA_PASSWORD` in `.env`)

## Dashboards

Ein vorkonfiguriertes Dashboard "Agent Metrics" ist bereits enthalten und zeigt:
- CPU- und Speicherauslastung der Agenten.
- Anzahl der verarbeiteten Tasks.
- Fehlerübersicht aus den Logs.

## Sicherheitskonfiguration für Produktion

Für den produktiven Einsatz sollten folgende Schritte durchgeführt werden:

1.  **Passwort ändern**: Setzen Sie ein starkes `GRAFANA_PASSWORD` in Ihrer `.env`.
2.  **Netzwerk einschränken**: Standardmäßig sind die Ports 3000 (Grafana), 9090 (Prometheus) und 3100 (Loki) offen. In einer produktiven Umgebung sollten diese Ports hinter einem Reverse Proxy (z.B. Nginx mit Auth) liegen oder nur an `127.0.0.1` gebunden werden.
3.  **Persistence**: Die Daten für Prometheus und Grafana werden in Docker Volumes gespeichert (`prometheus_data`). Stellen Sie sicher, dass diese Volumes regelmäßig gesichert werden.
4.  **Ressourcen-Limits**: In der `docker-compose.yml` sind bereits grundlegende Limits konfiguriert, um zu verhindern, dass das Monitoring den Host überlastet.
