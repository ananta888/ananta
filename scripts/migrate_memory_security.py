#!/usr/bin/env python3
"""Bounded operator migration. Default is a non-writing namespace-admission preview."""

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--after-id")
    parser.add_argument("--batch-id")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--apply", action="store_true")
    operation.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    if not args.database_url or (args.apply or args.rollback) and not args.batch_id:
        print(json.dumps({"status": "blocked", "reason_code": "memory_migration_configuration_required"}))
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlmodel import create_engine

    from agent.repositories.memory_migration import MemoryMetadataMigrationRepository

    engine = None
    try:
        engine = create_engine(args.database_url)
        repository = MemoryMetadataMigrationRepository(engine)
        if args.rollback:
            result = repository.rollback(args.batch_id)
        else:
            result = repository.migrate(limit=args.limit, after_id=args.after_id, dry_run=not args.apply,
                                        batch_id=args.batch_id, admit_namespaces=True)
        print(json.dumps({"status": "ok", "classification": "technical_operator_observation",
                          "production_release_evidence": False, "result": result}, sort_keys=True))
        return 0
    except Exception as exc:
        # Database URLs and provider diagnostics may contain credentials.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("memory_") else (
            "memory_migration_unavailable"
        )
        print(json.dumps({"status": "blocked", "reason_code": reason}))
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
