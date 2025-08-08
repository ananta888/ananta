#!/bin/bash
set -e
#!/bin/bash

echo "Warte auf Datenbankverbindung..."
while ! pg_isready -h db -U postgres; do
  sleep 1
done
echo "Datenbank ist bereit!"

echo "Initialisiere Datenbankschemas..."
python -m src.db_setup

echo "Starte AI-Agent..."
python -m agent.ai_agent
echo "Warte auf Datenbankverbindung..."
# Warte auf die Datenbank
until pg_isready -h db -U postgres; do
  echo "Warte auf Datenbank..."
  sleep 2
done
echo "Datenbank ist bereit!"

# Initialisiere die Datenbankschemas
echo "Initialisiere Datenbankschemas..."
python -m src.db_setup

# Warte auf den Controller-Service
echo "Warte auf Controller-Service..."
until curl -s http://controller:8081/health > /dev/null; do
  echo "Controller noch nicht bereit..."
  sleep 2
done
echo "Controller ist bereit!"

# Starte den AI-Agent
echo "Starte AI-Agent-Service..."
exec python -m agent.ai_agent
