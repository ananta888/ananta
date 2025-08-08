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

# PostgreSQL-Client und curl für Healthchecks installieren
RUN apt-get update && \
    apt-get install -y postgresql-client curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Benutzer "node" und Gruppe "node" anlegen
RUN addgroup --system node && adduser --system --ingroup node node

RUN mkdir -p /home/node && chown -R node:node /home/node
ENV HOME=/home/node

# Kopiere den Quellcode
COPY . /app

EXPOSE 5000
# Mehrstufiger Build für das Ananta-System

# Gemeinsame Basis für alle Stufen
FROM python:3.13-slim AS base

# Installiere grundlegende Utilities, wenn INSTALL_EXTRAS=true
ARG INSTALL_EXTRAS=false
RUN if [ "$INSTALL_EXTRAS" = "true" ] ; then \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    bash \
    ca-certificates \
    git \
    gnupg \
    wget \
    netcat-openbsd \
    coreutils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* ; \
    fi

# Arbeitsverzeichnis festlegen
WORKDIR /app

# Kopiere die Anforderungen zuerst, um Caching beim erneuten Aufbau zu verbessern
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Controller-Stufe
FROM base AS controller_stage

# Node.js für Frontend-Operationen installieren - direkt von NodeSource binaries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && echo "Prüfe Node.js und npm Installation:" \
    && node --version \
    && npm --version \
    && npm config set update-notifier false \
    && npm config set fund false \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Erstelle Frontend-Verzeichnisse für volumes
RUN mkdir -p /app/frontend/node_modules /app/frontend/dist && \
    chmod -R 777 /app/frontend/node_modules /app/frontend/dist

# Kopiere den Anwendungscode
COPY . .

# Installiere npm-Pakete und baue das Frontend
RUN cd frontend && npm install && npm run build

# Anbieten von Port 8081
EXPOSE 8081

# Controller starten
CMD ["python", "-m", "controller.controller"]

# AI-Agent-Stufe
FROM base AS ai_agent_stage

# PostgreSQL-Client für pg_isready installieren
RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Kopiere den Anwendungscode
COPY . .

# Anbieten von Port 5000
EXPOSE 5000

# AI-Agent starten
CMD ["/bin/bash", "-c", "chmod +x /app/entrypoint-ai-agent.sh && /app/entrypoint-ai-agent.sh"]

# Playwright-Teststufe
FROM mcr.microsoft.com/playwright:v1.40.0-jammy AS playwright_v1_40

# Kopiere den Code und führe die Tests aus
WORKDIR /app
COPY . .

# Arbeitsverzeichnis auf Frontend setzen
WORKDIR /app/frontend

# Installiere npm-Pakete
RUN npm ci

# Installiere Playwright und die Browser
RUN npm install -D @playwright/test && \
    npx playwright install --with-deps chromium
# --------------------------------------------------------------

# Stage "playwright": für E2E-Tests
FROM mcr.microsoft.com/playwright:v1.45.0-jammy AS playwright_v1_45

WORKDIR /app

# Setze Umgebungsvariablen für npm
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_PREFER_OFFLINE=true \
    NPM_CONFIG_LOGLEVEL=verbose \
    NODE_OPTIONS="--max-old-space-size=4096"

# Nützliche Tools für Debugging und Netzwerktests
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget curl tree htop procps iputils-ping net-tools dnsutils && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    npm --version && \
    node --version

# Stelle sicher, dass Verzeichnisse existieren und Berechtigungen stimmen
RUN mkdir -p /app/frontend/node_modules /app/frontend/dist && \
    chmod -R 777 /app/frontend

# Füge Hilfsskripte hinzu
RUN echo '#!/bin/bash\necho "Waiting for $1 to be ready..."\nwhile ! wget -q --spider "$1"; do\n  echo "Service not ready, retrying..."\n  sleep 2\ndone\necho "Service is ready"' > /usr/local/bin/wait-for-service && \
    chmod +x /usr/local/bin/wait-for-service && \
    echo '#!/bin/bash\necho "Prüfe Netzwerkverbindungen..."\nping -c 1 controller || echo "Controller nicht erreichbar"\nping -c 1 ai-agent || echo "AI-Agent nicht erreichbar"\ncurl -s http://controller:8081/health || echo "Controller-Health nicht erreichbar"\ncurl -s http://ai-agent:5000/health || echo "AI-Agent-Health nicht erreichbar"' > /usr/local/bin/check-network && \
    chmod +x /usr/local/bin/check-network

# Konfiguriere npm für Docker-Umgebung
RUN echo "bin-links=true\nfund=false\nupdate-notifier=false\nunsafe-perm=true\nscripts-prepend-node-path=true\nnetwork-timeout=120000\nfetch-retries=5\nfetch-retry-factor=2\nfetch-retry-mintimeout=20000\nfetch-retry-maxtimeout=120000\nno-optional=true\nmaxsockets=4\nregistry=https://registry.npmjs.org/\nloglevel=verbose\nprefer-offline=true\nprefer-reduced-size=true\nno-audit=true" > /app/.npmrc

# Wir installieren die Abhängigkeiten erst zur Laufzeit, um Docker-Caching besser zu nutzen
# und mögliche Konflikte zu vermeiden