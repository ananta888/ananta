# Stage “base” mit allen gemeinsam genutzten Tools und Bibliotheken
FROM python:3.11-slim AS base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DEBIAN_FRONTEND=noninteractive

# System-Dependencies + Node.js installieren
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Gemeinsame Python-Dependencies
RUN pip install --no-cache-dir flask requests pydantic pyyaml psycopg2-binary


# --------------------------------------------------------------

# Stage “controller”: enthält Frontend-Build + Controller-Code
FROM base AS controller

# Benutzer "node" und Gruppe "node" anlegen
RUN addgroup --system node && adduser --system --ingroup node node

# Quellcode kopieren
COPY . /app

# Frontend bauen
RUN cd frontend && npm install --unsafe-perm
RUN chown -R $(id -u):$(id -g) /app/frontend
RUN chown -R node:node /app/frontend
RUN chown -R node:node /app/frontend/node_modules
RUN chown -R node:node /app

USER node
WORKDIR /app

EXPOSE 8081

# Controller starten
CMD ["python", "-m", "controller.controller"]

# --------------------------------------------------------------

# Stage “ai-agent”: nur Python-Agent
FROM base AS ai-agent

# Benutzer "node" und Gruppe "node" anlegen
RUN addgroup --system node && adduser --system --ingroup node node

RUN mkdir -p /home/node && chown -R node:node /home/node
ENV HOME=/home/node

EXPOSE 5000

# --------------------------------------------------------------

# Stage "playwright": für E2E-Tests
FROM mcr.microsoft.com/playwright:v1.45.0-jammy AS playwright

WORKDIR /app

# Nützliche Tools für Debugging und Netzwerktests
RUN apt-get update && \
    apt-get install -y wget curl tree htop procps && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Stelle sicher, dass Verzeichnisse existieren und Berechtigungen stimmen
RUN mkdir -p /app/frontend/node_modules && \
    mkdir -p /app/frontend/dist && \
    chmod -R 777 /app/frontend

# Füge ein Health-Check-Script hinzu, um auf Backend-Services zu warten
RUN echo '#!/bin/bash\necho "Waiting for $1 to be ready..."\nwhile ! wget -q --spider "$1"; do\n  echo "Service not ready, retrying..."\n  sleep 2\ndone\necho "Service is ready"' > /usr/local/bin/wait-for-service && \
    chmod +x /usr/local/bin/wait-for-service

# Kopiere package.json und installiere Dependencies im Voraus
COPY ./frontend/package.json /app/frontend/
COPY ./frontend/package-lock.json* /app/frontend/
WORKDIR /app/frontend

# Installiere NPM-Pakete und Playwright-Browser
RUN npm ci && \
    npm install -D @playwright/test && \
    npx playwright install --with-deps chromium && \
    # Stelle sicher, dass die Ausführungsrechte korrekt sind
    chmod -R +x /app/frontend/node_modules/.bin

# Kopiere das Test-Script und mache es ausführbar
COPY run-tests-docker.sh /app/run-tests.sh
RUN chmod +x /app/run-tests.sh

# Testen, ob Playwright funktioniert
RUN echo "import { test } from '@playwright/test'; console.log('Playwright-Version:', test.info);" > test.mjs && \
    node test.mjs && \
    rm test.mjs

# Stelle sicher, dass die Netzwerk-Verbindungen korrekt konfiguriert sind
RUN echo '#!/bin/bash\necho "Prüfe Netzwerkverbindungen..."\nping -c 1 controller || echo "Controller nicht erreichbar"\nping -c 1 ai-agent || echo "AI-Agent nicht erreichbar"\ncurl -v http://controller:8081/health || echo "Controller-Health nicht erreichbar"\ncurl -v http://ai-agent:5000/health || echo "AI-Agent-Health nicht erreichbar"' > /usr/local/bin/check-network && \
    chmod +x /usr/local/bin/check-network