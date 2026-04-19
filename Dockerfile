FROM python:3.11.15-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47

ARG DEBIAN_SNAPSHOT=20260406T000000Z

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    OPENCODE_AI_VERSION=1.14.18

# Installiere System-Abhaengigkeiten aus einem festen Debian-Snapshot.
RUN printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot \
    && printf '%s\n' \
        'Types: deb' \
        "URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" \
        'Suites: bookworm bookworm-updates' \
        'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        '' \
        'Types: deb' \
        "URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}" \
        'Suites: bookworm-security' \
        'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
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
