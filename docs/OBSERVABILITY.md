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

Folgende vorkonfigurierte Dashboards sind enthalten:

### 1. Agent Metrics & Logs
- Agent-Logs in Echtzeit (Loki)
- Filterung nach Container

### 2. Task Metrics
- Task-Status (Received, Completed, Failed)
- Task-Throughput über Zeit
- Task-Fehlerquote
- Shell Pool Status (Total, Busy, Free)
- Retry-Zähler

### 3. LLM & RAG Performance
- LLM Call Duration (P50, P90, P99)
- RAG Retrieval Duration
- RAG Requests nach Modus
- RAG Chunks Selected
- HTTP Request Duration nach Endpoint

### 4. System Health
- CPU-Auslastung (aktuell + Verlauf)
- Memory-Usage (aktuell + Verlauf)
- Retries als Fehlerindikator
- Task-Aktivität (5-Minuten-Buckets)

## Startup-Metriken

Der Flask-Startup veroeffentlicht neben der Gesamtdauer auch einzelne Phasen:

- `app_startup_duration_seconds`
- `app_startup_phase_duration_seconds{phase,status}`
- `app_startup_phase_total{phase,status}`
- `app_startup_failures_total{phase,error_type}`

Die Phasen entsprechen den benannten Bootstrap-Schritten in `agent.ai_agent.create_app()`, zum Beispiel `database`, `blueprints`, `extensions`, `core_services` und `background_services`. Fehler werden mit Phase und Fehlertyp geloggt, damit Compose-, Plugin-, DB- und Registry-Probleme schneller voneinander unterscheidbar sind.

## Sicherheitskonfiguration für Produktion

Für den produktiven Einsatz sollten folgende Schritte durchgeführt werden:

1.  **Passwort ändern**: Setzen Sie ein starkes `GRAFANA_PASSWORD` in Ihrer `.env`.
2.  **Netzwerk einschränken**: Standardmäßig sind die Ports 3000 (Grafana), 9090 (Prometheus) und 3100 (Loki) offen. In einer produktiven Umgebung sollten diese Ports hinter einem Reverse Proxy (z.B. Nginx mit Auth) liegen oder nur an `127.0.0.1` gebunden werden.
3.  **Persistence**: Die Daten für Prometheus und Grafana werden in Docker Volumes gespeichert (`prometheus_data`). Stellen Sie sicher, dass diese Volumes regelmäßig gesichert werden.
4.  **Ressourcen-Limits**: In der `docker-compose.yml` sind bereits grundlegende Limits konfiguriert, um zu verhindern, dass das Monitoring den Host überlastet.
# Knowledge Indexing

Zusatzmetriken fuer `rag-helper`-gestuetzte Artefakt- und Collection-Indizes:

- `knowledge_index_runs_total{scope,status,profile}`
- `knowledge_index_duration_seconds{scope,profile}`
- `knowledge_index_active_jobs`
- `knowledge_retrieval_chunks_selected`

Zu beobachten:

- steigende `failed`-Runs fuer ein bestimmtes Profil
- lange `knowledge_index_duration_seconds` bei `deep_code`
- dauerhaft positive `knowledge_index_active_jobs` ohne Statusabschluss
- unerwartet `0` Knowledge-Chunks trotz aktiver Collections

Fuer Debugging:

- `GET /knowledge/index-profiles`
- `GET /knowledge/index-jobs/<job_id>`
- `GET /artifacts/<artifact_id>/rag-status`
- `GET /artifacts/<artifact_id>/rag-preview`
