#!/bin/bash
set -e

echo "Warte auf Datenbankverbindung..."
# Warte auf die Datenbank
until pg_isready -h db -U postgres; do
  echo "Warte auf Datenbank..."
  sleep 2
done
echo "Datenbank ist bereit!"

# Initialisiere die Datenbankschemas
echo "Initialisiere Datenbankschemas..."
python -m src.db_setup

echo "System-Information:"
node --version
npm --version

echo "Prüfe Verzeichnisberechtigungen:"
ls -la /app/frontend
ls -la /app/frontend/node_modules || echo "Node-Modules nicht gefunden"

if [ "$RUN_TESTS" = "true" ]; then
  echo "Bereite Tests vor..."
  cd /app/frontend
  npm ci --no-audit --prefer-offline
  npx playwright install --with-deps chromium
  NODE_OPTIONS="--max-old-space-size=4096 --experimental-vm-modules" npx playwright test
fi

echo "Starte Controller-Anwendung..."
cd /app
exec python -m controller.controller
