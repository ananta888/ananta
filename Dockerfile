# Basis mit Python + Node für Builds
FROM python:3.11-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
# Python-Abhängigkeiten installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Frontend Build Stage ----------------------------------------------------
FROM base AS frontend_build
WORKDIR /app/frontend
# Nur package-Dateien zuerst zum Cache nutzen
COPY frontend/package*.json ./
RUN npm ci
# Restliche Quellen
COPY frontend/ ./
RUN npm run build

# ---- Controller Stage (liefert Frontend aus) ---------------------------------
FROM base AS controller_stage
WORKDIR /app
# Backend/Controller-Code und gemeinsame Module
COPY controller/ /app/controller/
COPY src/ /app/src/
COPY common/ /app/common/
COPY config.json /app/config.json

# Frontend-Build ins Image legen
COPY --from=frontend_build /app/frontend/dist /app/frontend/dist

# Prod Webserver: gunicorn (schlank und robust)
RUN pip install --no-cache-dir gunicorn

ENV FRONTEND_DIST=/app/frontend/dist \
    LOG_LEVEL=INFO
EXPOSE 8081
# WSGI-Entry zeigt auf controller/controller.py
CMD ["gunicorn", "-b", "0.0.0.0:8081", "controller.controller:app"]

# ---- AI-Agent Stage ----------------------------------------------------------
FROM base AS ai_agent_stage
WORKDIR /app
# Agent und gemeinsame Module
COPY agent/ /app/agent/
COPY src/ /app/src/
COPY common/ /app/common/
COPY config.json /app/config.json
ENV LOG_LEVEL=INFO
EXPOSE 5000
CMD ["python", "-u", "agent/ai_agent.py"]

