#!/bin/bash

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
