FROM python:3.11-slim

# Installiere System-Abhängigkeiten einmalig beim Build
RUN apt-get update && apt-get install -y \
    curl \
    net-tools \
    iputils-ping \
    traceroute \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kopiere Anforderungen und installiere Python-Pakete (Caching nutzen)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Den Rest des Codes kopieren
COPY . .

# Default Environment (kann via docker-compose überschrieben werden)
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=0

# Startbefehl
CMD ["sh", "-c", "alembic upgrade head && python -m agent.ai_agent"]
