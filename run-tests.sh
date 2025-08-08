#!/bin/bash
#!/bin/bash
set -e

echo "===== Prüfe Netzwerkverbindungen ====="
echo "Controller-Service auf Port 8081:"
netstat -tulpn | grep 8081 || echo "Kein lokaler Port 8081 gefunden"

echo "\nVersuche Controller über DNS zu erreichen:"
ping -c 1 controller || echo "Controller nicht per ping erreichbar"

echo "\nVersuche AI-Agent über DNS zu erreichen:"
ping -c 1 ai-agent || echo "AI-Agent nicht per ping erreichbar"

echo "\nPrüfe HTTP-Verbindung zum Controller:"
curl -v http://controller:8081/health || echo "Controller HTTP-Endpunkt nicht erreichbar"

echo "\n===== Starte Playwright-Tests ====="
cd /app/frontend

# Stelle sicher, dass die Konfiguration die richtige URL verwendet
echo "Playwright-Konfiguration:"
cat playwright.config.js

# Führe die Tests aus
export NODE_OPTIONS="--experimental-vm-modules"
npx playwright test
cd "$(dirname "$0")/frontend"

# Stelle sicher, dass Playwright-Abhängigkeiten installiert sind
npm ci
npm install -D @playwright/test
npx playwright install --with-deps

# Setze Umgebungsvariablen für Tests
export PLAYWRIGHT_BASE_URL=http://localhost:8081
export PLAYWRIGHT_SKIP_WEBSERVER=1

# Führe Tests aus
npx playwright test
