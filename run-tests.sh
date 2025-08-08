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

cd /app/frontend

# Installiere die benötigten Abhängigkeiten
echo "Installiere Abhängigkeiten..."
npm ci

# Installiere Playwright
echo "Installiere Playwright..."
npm install -D @playwright/test

# Installiere Browser-Abhängigkeiten
echo "Installiere Browser-Abhängigkeiten..."
npx playwright install --with-deps chromium

# Baue das Frontend
echo "Baue das Frontend..."
npm run build

# Warten auf Frontend und API
echo "Warte auf vollständige Verfügbarkeit des Frontends..."
sleep 5

# Stelle sicher, dass die Konfiguration die richtige URL verwendet
echo "Playwright-Konfiguration:"
cat playwright.config.js

# Setze Umgebungsvariablen für Tests
export PLAYWRIGHT_BASE_URL=http://controller:8081
export PLAYWRIGHT_SKIP_WEBSERVER=1

# Führe die Tests mit verbesserten Optionen aus
echo "Starte Tests mit verbesserter Konfiguration..."
NODE_OPTIONS="--max-old-space-size=4096 --experimental-vm-modules" npx playwright test --retries=3 --timeout=90000 --workers=1

# Prüfe den Exit-Code
if [ $? -ne 0 ]; then
  echo "Tests fehlgeschlagen!"
  exit 1
fi

echo "Tests erfolgreich abgeschlossen!"
exit 0
