# Backend Overview

This directory contains the backend components of Ananta.

## Structure

- `agents/` – dataclasses and prompt template utilities.
- `controller/` – Flask controller agent and HTTP routes.
- `models/` – model pool and related abstractions.
- `db/` – database migrations and helpers.

## Running the controller

The controller exposes HTTP endpoints for agents and the dashboard. Start it locally with:

```bash
python -m src.controller.controller
```

Set `DATABASE_URL` to point at your PostgreSQL instance. For development, `docker-compose up` will provision one automatically.
## API Overview

| Endpoint | Purpose |
|----------|---------|
| `/config` | Fetch current controller configuration |
| `/next-config` | Retrieve next agent task |
| `/agent/<name>/log` | Read logs for a specific agent |


## Testing

Unit tests live under `tests/`. Run them via:

```bash
python -m unittest
```

## Getting Started

```bash
# install dependencies
pip install -r requirements.txt

# run controller for development
python -m src.controller.controller
```

See the root README for overarching project details.
