#!/usr/bin/env python3
"""Create a private encrypted backup of the local Ananta stack."""

from ananta_backup.cli import backup_main


if __name__ == "__main__":
    raise SystemExit(backup_main())
