#!/bin/bash
set -e

# Diagnoseskript für Node.js/npm-Probleme
echo "===== Node.js/npm Diagnose-Tool ====="

# System-Informationen anzeigen
echo "\n[1] System-Informationen:"
cat /etc/os-release
uname -a

# Node.js-Version prüfen
echo "\n[2] Node.js-Installation:"
which node || echo "Node.js nicht gefunden"
node --version || echo "Node.js-Version konnte nicht ermittelt werden"

# npm-Version prüfen
echo "\n[3] npm-Installation:"
which npm || echo "npm nicht gefunden"
npm --version || echo "npm-Version konnte nicht ermittelt werden"

# Paketquellen prüfen
echo "\n[4] Konfigurierte APT-Quellen:"
ls -la /etc/apt/sources.list.d/
cat /etc/apt/sources.list.d/nodesource.list 2>/dev/null || echo "Keine nodesource.list gefunden"

# Prüfen auf Konflikte
echo "\n[5] Paketabhängigkeiten prüfen:"
apt-cache policy nodejs npm

# npm-Konfiguration anzeigen
echo "\n[6] npm-Konfiguration:"
npm config list || echo "npm-Konfiguration konnte nicht angezeigt werden"

# Netzwerkverbindung testen
echo "\n[7] Registry-Verbindung testen:"
curl -Is https://registry.npmjs.org/ | head -n 1 || echo "Keine Verbindung zur npm-Registry"

# Cache-Verzeichnisse prüfen
echo "\n[8] npm-Cache prüfen:"
npm cache verify || echo "npm-Cache konnte nicht überprüft werden"

echo "\n===== Diagnose abgeschlossen ====="

# Empfehlungen für Node.js-Installation
echo "\nEmpfehlungen für Node.js-Installation:"
echo "1. Node.js über nodesource-Repository neu installieren:"
echo "   curl -fsSL https://deb.nodesource.com/setup_18.x | bash -"
echo "   apt-get install -y nodejs"
echo "2. Oder manuelle Installation über nvm:"
echo "   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
echo "   nvm install 18"
