# Backend Overview

This directory contains the backend components of Ananta.

## Structure

- `agents/` – dataclasses and prompt template utilities.
- `controller/` – Flask controller agent and HTTP routes.
- `models/` – model pool and related abstractions.
- `db/` – database migrations and helpers.

## Getting Started

```bash
# install dependencies
pip install -r requirements.txt

# run controller for development
python -m src.controller.controller
```

See the root README for overarching project details.
