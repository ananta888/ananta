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
#!/bin/bash
set -e

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
# Warte auf den Controller-Service
echo "Warte auf Controller-Service..."
until curl -s http://controller:8081/health > /dev/null; do
  echo "Controller noch nicht bereit..."
  sleep 2
done

echo "Controller ist bereit!"

# Starte den AI-Agent
echo "Starte AI-Agent-Service..."
exec python -m agent.ai_agent
