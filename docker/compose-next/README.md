# Compose Next (Prototyp)

Direkt startbare Compose-Dateien ohne Startskript-Pflicht.

## Prinzip

- `compose.base.yml`: gemeinsame Basis-Definitionen (Templates)
- `compose.stack.*.yml`: jeweils einzeln startbare Stacks, die per `extends` die Basis ziehen

Damit ist jede Stack-Datei direkt nutzbar mit genau einem Compose-Aufruf.

## Direktstart

```bash
# leicht: SQLite
cd docker/compose-next
docker compose -f compose.stack.quickstart.yml up -d

# full: Postgres + Redis
cd docker/compose-next
docker compose -f compose.stack.full.yml up -d

# distributed: full + zusätzliche Worker
cd docker/compose-next
docker compose -f compose.stack.distributed.yml up -d
```

Optional in `distributed`:

```bash
# Ollama nur bei Bedarf
cd docker/compose-next
docker compose -f compose.stack.distributed.yml --profile ollama up -d
```

Stoppen:

```bash
cd docker/compose-next
docker compose -f compose.stack.full.yml down
```

## Architektur

Hub bleibt Control Plane, Worker führen delegierte Tasks aus. Keine Worker-zu-Worker-Orchestrierung.
