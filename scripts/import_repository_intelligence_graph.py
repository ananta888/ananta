#!/usr/bin/env python3
"""RIG-012 CLI entrypoint.

Thin wrapper around :mod:`worker.retrieval.codecompass_rig_importer`.
The CLI is intentionally minimal: validate (default) or
validate-and-persist (--write-index).

Persistent writes must flow through the Hub task system
(CCRIG-DD-009). This script is meant for diagnostics and for
bootstrapping manual fixtures in development.
"""
from __future__ import annotations

from worker.retrieval.codecompass_rig_importer import main


if __name__ == "__main__":
    raise SystemExit(main())