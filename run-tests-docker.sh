#!/bin/bash
set -e

# Robust wait function with visible progress
wait_for_service() {
  echo "Warte auf $1 ($2)..."
  timeout=120
  interval=5
  elapsed=0
  while [ $elapsed -lt $timeout ]; do
    if curl -s $2 > /dev/null; then
      echo "$1 ist bereit!"
      return 0
    fi
    sleep $interval
    elapsed=$((elapsed + interval))
    echo "Warte weiter auf $1 ($elapsed/$timeout Sekunden)..."
  done
  echo "Timeout beim Warten auf $1!"
  return 1
}

# Warte auf die Services
wait_for_service "Controller-Service" "http://controller:8081/config"
wait_for_service "AI-Agent-Service" "http://ai-agent:5000/health"

echo "Installiere Abhängigkeiten..."
npm ci --no-bin-links
npm install -D @playwright/test

echo "Installiere Browser..."
npx playwright install --with-deps chromium

# Debug-Informationen
echo "Controller Config-Endpunkt Antwort:"
curl -v http://controller:8081/config || true
echo -e "\nAI-Agent Health-Endpunkt Antwort:"
curl -v http://ai-agent:5000/health || true
echo -e "\n"

echo "Starte Playwright-Tests mit erhöhtem Timeout..."
# Setze die korrekte Base-URL für Tests in Docker
export PLAYWRIGHT_BASE_URL=http://controller:8081
# Erhöhe den Test-Timeout auf 60 Sekunden
npx playwright test --timeout=60000

EXIT_CODE=$?
echo "Playwright-Tests abgeschlossen mit Exit-Code: $EXIT_CODE"
exit $EXIT_CODE
