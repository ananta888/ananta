#!/bin/bash
# Minimal lokaler Worker-Start (ohne Docker) – nutzt laufende Docker-DB und Redis
# Voraussetzung: Docker-Container ananta-postgres-1 und ananta-redis-1 laufen
#                Hub läuft unter HUB_URL (Docker oder lokal)
# Aufruf: bash scripts/run_local_worker.sh [WORKER_NAME] [PORT] [HUB_URL]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

WORKER_NAME="${1:-worker-local}"
PORT="${2:-5010}"
HUB_URL="${3:-http://localhost:5000}"
DB_PORT="${DB_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"

export PYTHONPATH="$SCRIPT_DIR"
export PYTHONUNBUFFERED=1
export FLASK_DEBUG=0

# Datenbankverbindung auf localhost (Docker mapped ports)
export DATABASE_URL="${DATABASE_URL:-postgresql://ananta:ananta_password@localhost:${DB_PORT}/ananta}"
export REDIS_URL="${REDIS_URL:-redis://localhost:${REDIS_PORT}/0}"

# Worker-Identität
export ROLE=worker
export AGENT_NAME="$WORKER_NAME"
export AGENT_URL="http://localhost:${PORT}"
export HUB_URL="$HUB_URL"

# LMStudio (Windows-Host)
export LMSTUDIO_URL="${LMSTUDIO_URL:-http://192.168.178.100:1234/v1}"
export LMSTUDIO_API_MODE=chat
export DEFAULT_PROVIDER=lmstudio
export DEFAULT_MODEL=auto

# Auth
export SECRET_KEY="${SECRET_KEY:-ananta-dev-shared-secret-change-me}"
export INITIAL_ADMIN_USER="${INITIAL_ADMIN_USER:-admin}"
export INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-test123}"

# Runtime
export HTTP_TIMEOUT=300
export COMMAND_TIMEOUT=300
export ANANTA_RUNTIME_PROFILE=dev

export PORT="$PORT"
echo "=== Ananta Worker '${WORKER_NAME}' startet auf http://localhost:${PORT} ==="
echo "    Hub:   $HUB_URL"
echo "    DB:    $DATABASE_URL"
echo "    Redis: $REDIS_URL"
echo "    LLM:   $LMSTUDIO_URL"
echo ""

python -m agent.ai_agent
