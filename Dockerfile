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
RUN pip install --no-cache-dir flask requests pydantic

# --------------------------------------------------------------

# Stage “controller”: enthält Frontend-Build + Controller-Code
FROM base AS controller

# Quellcode kopieren
COPY . /app

# Frontend bauen
RUN cd frontend && \
    npm install

EXPOSE 8081

# Controller starten
CMD ["python", "-m", "controller.controller"]

# --------------------------------------------------------------

# Stage “ai-agent”: nur Python-Agent
FROM base AS ai-agent

EXPOSE 5000


