#!/usr/bin/env bash
# SQLite shell for the live hub database.
# Usage: ./scripts/db_shell.sh [sql_statement]
#
# The hub container mounts ./data/hub as /app/data, so the live DB is at
# data/hub/ananta-hub.db — NOT data/ananta-hub.db.

DB="/home/krusty/ananta/data/hub/ananta-hub.db"

if [ ! -f "$DB" ]; then
  echo "ERROR: DB not found at $DB" >&2
  exit 1
fi

if [ -n "$1" ]; then
  sqlite3 "$DB" "$1"
else
  sqlite3 "$DB"
fi
