# Distributed Deployment (v1.0)

Diese Variante erweitert den bestehenden Hub/Worker-Stack um weitere Worker-Nodes.

## Ziele

- Multi-Node Worker-Setup
- Lastverteilung ueber Hub-Orchestrierung
- High-Availability-Optionen fuer stateless Worker

## Start

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml up -d --build
```

## Enthaltene Nodes

- `ai-agent-hub`
- `ai-agent-alpha`
- `ai-agent-beta`
- `ai-agent-gamma`
- `ai-agent-delta`

## Empfohlene Konfiguration

- Hub als zentraler Router/Orchestrator (`ROLE=hub`)
- Worker-Nodes stateless betreiben (eigene `data/<node>` Volumes)
- Redis aktivieren fuer schnellere Status-/Task-Koordination
- Nginx als Edge-Layer beibehalten

## High-Availability Hinweise

- Mehrere Worker reduzieren Ausfallrisiko einzelner Instanzen.
- Bei Host-Ausfall ist zusaetzlich infra-seitiges HA noetig (z.B. VM/Node-Failover).
- Fuer echtes Active/Active auf Hub-Ebene wird ein externer Leader-/Lock-Mechanismus empfohlen.

## Smoke-Test

```bash
docker compose -f docker-compose.base.yml -f docker-compose.yml -f docker-compose.distributed.yml ps
curl -fsS http://localhost:${HUB_PORT:-5000}/health
```
