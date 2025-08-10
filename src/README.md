# Backend Overview

This directory contains the backend components of Ananta.

## Structure

- `agents/` – dataclasses and prompt template utilities.
- `controller/` – Flask controller agent and HTTP routes.
- `models/` – model pool and related abstractions.
- `db/` – database migrations and helpers.

## Database Models and ORM

Ananta uses **SQLAlchemy** models to persist configuration, tasks and logs. Core models live in `src/models.py` while migrations and session helpers reside in `src/db/`. A typical model definition looks like:

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String)
```

`db_setup.py` initialises the engine using the `DATABASE_URL` environment variable and exposes `session_scope()` for transactional operations.

## Running the controller

The controller exposes HTTP endpoints for agents and the dashboard. Start it locally with:

```bash
python -m src.controller.controller
```

Set `DATABASE_URL` to point at your PostgreSQL instance. For development, `docker-compose up` will provision one automatically.

## Authentication

API routes can be protected with a simple middleware that checks for an `X-API-Key` header. Example usage:

```python
from functools import wraps
from flask import request, abort

API_KEY = "change-me"

def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-API-Key") != API_KEY:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper

@app.get("/protected")
@require_api_key
def protected():
    return {"status": "ok"}
```

Clients should include the header `X-API-Key: <value>` in their requests.

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
