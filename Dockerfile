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

# Stellen sicher, dass wir Playwright richtig verwenden können
RUN mkdir -p /app/frontend/node_modules && \
    mkdir -p /app/frontend/dist && \
    chmod -R 777 /app/frontend