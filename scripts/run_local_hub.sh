#!/bin/bash
# Minimal local Hub-Start (ohne Docker) – nutzt laufende Docker-DB und Redis
# Voraussetzung: Docker-Container ananta-postgres-1 und ananta-redis-1 laufen
# Aufruf: bash scripts/run_local_hub.sh [PORT]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-5000}"
DB_PORT="${DB_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"

export PYTHONPATH="$SCRIPT_DIR"
export PYTHONUNBUFFERED=1
export FLASK_DEBUG=0

# Datenbankverbindung auf localhost (Docker mapped ports)
export DATABASE_URL="${DATABASE_URL:-postgresql://ananta:ananta_password@localhost:${DB_PORT}/ananta}"
export REDIS_URL="${REDIS_URL:-redis://localhost:${REDIS_PORT}/0}"

# Hub-Identität
export ROLE=hub
export AGENT_NAME=hub
export AGENT_URL="http://localhost:${PORT}"
export HUB_CAN_BE_WORKER=0

# LMStudio (Windows-Host)
export LMSTUDIO_URL="${LMSTUDIO_URL:-http://192.168.178.100:1234/v1}"
export LMSTUDIO_API_MODE=chat
export DEFAULT_PROVIDER=lmstudio
export DEFAULT_MODEL=auto

# Auth
export SECRET_KEY="${SECRET_KEY:-ananta-dev-shared-secret-change-me}"
export INITIAL_ADMIN_USER="${INITIAL_ADMIN_USER:-admin}"
export INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-test123}"
export AUTH_TEST_ENDPOINTS_ENABLED=0

# Runtime
export HTTP_TIMEOUT=300
export COMMAND_TIMEOUT=300
export ANANTA_RUNTIME_PROFILE=dev

export PORT="$PORT"
echo "=== Ananta Hub startet auf http://localhost:${PORT} ==="
echo "    DB:    $DATABASE_URL"
echo "    Redis: $REDIS_URL"
echo "    LLM:   $LMSTUDIO_URL"
echo ""

python -m agent.ai_agent
