#!/bin/bash

# Optimierungsscript für Playwright E2E Tests in Docker

# Setze Browser-Cache Verzeichnis
export PLAYWRIGHT_BROWSERS_PATH=/app/.playwright-cache

# Prüfe ob Browser bereits installiert ist
if [ ! -d "$PLAYWRIGHT_BROWSERS_PATH/chromium-" ]; then
  echo "Installiere Browser (nur beim ersten Lauf)..."
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=0 npx playwright install chromium --with-deps
else
  echo "Browser-Cache gefunden, überspringe Installation"
fi

# Führe nur Chromium-Tests aus (reduziert Testzeiten)
export PLAYWRIGHT_BROWSER=chromium

# Deaktiviere Debug-Features für schnellere Tests
export DEBUG=

# Führe Tests aus
npx playwright test "$@"
