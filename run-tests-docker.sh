#!/bin/bash
set -e

# Robust wait function with visible progress
wait_for_service() {
  echo "Warte auf $1 ($2)..."
  timeout=300  # Erhöhter Timeout auf 5 Minuten
  interval=5
  elapsed=0
  while [ $elapsed -lt $timeout ]; do
    # Zeige detaillierte Informationen beim Versuch
    echo "Versuche Verbindung zu $2..."
    curl -v $2 || true
    echo "" # Leere Zeile für bessere Lesbarkeit

    # Tatsächlicher Test mit silent curl
    if curl -s $2 > /dev/null; then
      echo "$1 ist bereit!"
      return 0
    fi

    # Ping-Test zur Diagnose
    echo "Ping-Test für $(echo $2 | cut -d/ -f3 | cut -d: -f1):"
    ping -c 1 $(echo $2 | cut -d/ -f3 | cut -d: -f1) || true

    sleep $interval
    elapsed=$((elapsed + interval))
    echo "Warte weiter auf $1 ($elapsed/$timeout Sekunden)..."
  done
  echo "Timeout beim Warten auf $1!"
  return 1
}

# Stelle sicher, dass das wait-for-it Skript ausführbar ist
chmod +x /app/frontend/wait-for-it.sh

# Warte auf die Services mittels TCP-Verbindungen
echo "Warte auf TCP-Verbindungen zu den Services..."
/app/frontend/wait-for-it.sh -t 60 controller:8081 || echo "Controller nicht erreichbar über TCP"
/app/frontend/wait-for-it.sh -t 30 ai-agent:5000 || echo "AI-Agent nicht erreichbar über TCP"

# Warte auf die HTTP-Endpunkte
wait_for_service "Controller-Service" "http://controller:8081/health"
wait_for_service "AI-Agent-Service" "http://ai-agent:5000/health"

echo "Installiere Abhängigkeiten..."
npm ci --no-bin-links
npm install -D @playwright/test

echo "Installiere Browser..."
npx playwright install --with-deps chromium

# Erstelle eine Datei für den Health-Check
echo '#!/bin/bash

echo "=== Netzwerk-Diagnose ==="
echo "Controller Health-Check:"
curl -v http://controller:8081/health
echo -e "\nAI Agent Health-Check:"
curl -v http://ai-agent:5000/health

echo -e "\n=== DNS-Auflösung ==="
ping -c 1 controller
ping -c 1 ai-agent

echo -e "\n=== Service-Discovery-Info ==="
echo "Docker-Netzwerke:"
cat /etc/hosts

echo -e "\nAktueller Pfad: $(pwd)"
echo "Umgebungsvariablen:"
env | grep PLAYWRIGHT
' > /app/frontend/healthcheck.sh
chmod +x /app/frontend/healthcheck.sh

# Führe den Health-Check aus
echo "=== Ausführlicher Health-Check ==="
/app/frontend/healthcheck.sh

# Debug-Informationen für die spezifischen Endpunkte
echo "=== API-Endpunkte Test ==="
echo "Controller Config-Endpunkt Antwort:"
curl -v http://controller:8081/config || echo "Config-Endpunkt nicht erreichbar"
echo -e "\nController UI-Endpunkt Test:"
curl -v http://controller:8081/ui/ || echo "UI-Endpunkt nicht erreichbar"
echo -e "\nAI-Agent Health-Endpunkt Antwort:"
curl -v http://ai-agent:5000/health || echo "Health-Endpunkt nicht erreichbar"
echo -e "\n"

echo "Starte Playwright-Tests mit erhöhtem Timeout..."
# Setze die korrekte Base-URL für Tests in Docker
export PLAYWRIGHT_BASE_URL=http://controller:8081
# Erhöhe den Test-Timeout auf 60 Sekunden
npx playwright test --timeout=60000

EXIT_CODE=$?
echo "Playwright-Tests abgeschlossen mit Exit-Code: $EXIT_CODE"
exit $EXIT_CODE
