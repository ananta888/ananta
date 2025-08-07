#!/bin/bash

echo "Warte auf Datenbankverbindung..."

# Warte auf die Datenbank
until pg_isready -h db -U postgres; do
  echo "Warte auf Datenbank..."
  sleep 2
done

echo "Datenbank ist bereit!"

# Starte den AI-Agent
exec python -m agent.ai_agent
