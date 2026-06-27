# Parallel Worker + Ollama Saturation

## Zielbild

Ananta nutzt getrennte Kapazitäts-Pools:
- Worker-Slots (`max_parallel_tasks` pro Worker)
- Ollama-Modell-Slots (`max_parallel_requests` pro `endpoint+model`)
- Subworker-Slots (`max_children_per_parent`)

Effektive Concurrency:
`min(security_policy_cap, worker_capacity, runtime_capacity, ollama_model_capacity)`

## Local Default Profil

- 2 Worker-Instanzen (`ananta-worker-1`, `ananta-worker-2`)
- 4 Worker-Slots pro Worker
- 4 parallele Ollama-Requests pro Modell

Beispiel:

```bash
DEFAULT_PROVIDER=ollama \
OLLAMA_URL=http://ollama:11434/api/generate \
ANANTA_WORKER_MAX_PARALLEL_TASKS=4 \
docker compose -f docker/old_way/docker-compose.base.yml -f docker/old_way/docker-compose.quickstart-no-ollama.yml -f docker/old_way/docker-compose.single-image-fullstack.yml --profile ollama up -d --build
```

## Low-VRAM Profil

Empfehlung bei knapper GPU/VRAM-Kapazität:
- `ANANTA_WORKER_MAX_PARALLEL_TASKS=2`
- `ANANTA_OLLAMA_MAX_PARALLEL=1` oder `2`
- kleinere Modelle bevorzugen

## High-Throughput Profil

- `ANANTA_WORKER_MAX_PARALLEL_TASKS=6` bis `8`
- `ANANTA_OLLAMA_MAX_PARALLEL=4` (oder höher nur nach Lasttest)
- mehrere Worker-Instanzen (`--scale`) oder feste Services

## Bottleneck-Diagnose

Typische Engpässe:
- Worker voll: `worker_parallel_capacity_exhausted`
- Ollama voll: `ollama_model_parallel_capacity_exhausted`
- Queue voll: `worker_queue_full` / `ollama_queue_full`
- Policy deny: `policy_denied_on_revalidation` / `stale_policy_decision`

Diagnostics-Endpunkte:
- `GET /api/worker-pool/status`
- `GET /api/worker-pool/leases`
- `GET /api/worker-pool/queues`
- `GET /api/worker-pool/ollama-models`
- `POST /api/worker-pool/cleanup-stale-leases` (admin)

## Troubleshooting

- Stuck leases: `POST /api/worker-pool/cleanup-stale-leases`
- Queue wächst: Worker-Kapazität vs. Ollama-Slots getrennt prüfen
- Starvation: Fairness-Limits und Parent-Task-Verteilung prüfen
- Policy-Änderungen: Queued Jobs werden beim Start revalidiert
