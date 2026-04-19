FROM python:3.11.15-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    OPENCODE_AI_VERSION=1.14.18

# Installiere System-Abhängigkeiten einmalig beim Build
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    net-tools \
    iputils-ping \
    traceroute \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode CLI once in the image so opencode backend is available after restarts.
RUN npm i -g "opencode-ai@${OPENCODE_AI_VERSION}" \
    && opencode --version | grep -F "${OPENCODE_AI_VERSION}" \
    && npm cache clean --force

WORKDIR /app

# Kopiere Release-Lockdatei und installiere Python-Runtime-Pakete (Caching nutzen)
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Den Rest des Codes kopieren
COPY . .

# Default Environment (kann via docker-compose überschrieben werden)
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=0

# Startbefehl: Verwende exec, um Signale korrekt an Python durchzureichen (PID 1 Problematik)
CMD ["sh", "-c", "alembic upgrade head && exec python -m agent.ai_agent"]
