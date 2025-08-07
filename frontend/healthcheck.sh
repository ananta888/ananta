#!/bin/bash

# Dieses Skript überprüft die Erreichbarkeit der Services

echo "=== Netzwerk-Diagnose ==="
echo "Controller Health-Check:"
curl -v http://controller:8081/health
echo -e "\nAI Agent Health-Check:"
curl -v http://ai-agent:5000/health

echo -e "\n=== DNS-Auflösung ==="
ping -c 1 controller
ping -c 1 ai-agent

echo -e "\n=== Service-Discovery-Info ==="
echo "Docker-Netzwerke:"
cat /etc/hosts

echo -e "\nAktueller Pfad: $(pwd)"
echo "Umgebungsvariablen:"
env | grep PLAYWRIGHT
