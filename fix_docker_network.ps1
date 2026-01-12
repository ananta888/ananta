# Dieses Skript versucht, Netzwerkprobleme mit Docker (insb. IPv6-Fehler unter WSL2) zu beheben.
# Es muss mit Administratorrechten ausgeführt werden, falls es Systemeinstellungen ändert.

Write-Host "Überprüfe Docker-Netzwerkkonfiguration..." -ForegroundColor Cyan

# 1. WSL2 Neustart (oft die einfachste Lösung)
Write-Host "`n1. Empfehlung: Starten Sie WSL2 neu, falls es Netzwerk-Hänger gibt." -ForegroundColor Yellow
Write-Host "Befehl: wsl --shutdown"

# 2. Versuch, das Image explizit über IPv4 zu laden (indem wir den Docker-Daemon anstupsen)
# Da wir den Daemon nicht direkt zwingen können, IPv4 zu nutzen, ohne die Config zu ändern,
# geben wir Anweisungen für die daemon.json.

$dockerConfigPath = "$env:USERPROFILE\.docker\daemon.json"
Write-Host "`n2. Überprüfe Docker-Konfiguration ($dockerConfigPath)..." -ForegroundColor Cyan

if (Test-Path $dockerConfigPath) {
    Write-Host "Konfigurationsdatei gefunden."
} else {
    Write-Host "Konfigurationsdatei nicht gefunden. Dies ist normal für Standard-Installationen."
}

Write-Host "`n3. DNS-Probleme beheben" -ForegroundColor Cyan
Write-Host "In den Docker Desktop Einstellungen (Settings -> Docker Engine):"
Write-Host 'Stellen Sie sicher, dass "dns": ["8.8.8.8", "1.1.1.1"] in der Konfiguration vorhanden ist, falls Pulls fehlschlagen.'

Write-Host "`n4. Manueller Pull-Versuch..." -ForegroundColor Cyan
try {
    docker pull postgres:15-alpine
    Write-Host "Erfolg: Image konnte geladen werden!" -ForegroundColor Green
} catch {
    Write-Host "Fehler beim Pull: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Versuche es mit einem alternativen Registry-Eintrag oder prüfen Sie Ihre Internetverbindung (IPv6)."
}

Write-Host "`n5. Workaround für das Projekt: SQLite nutzen" -ForegroundColor Cyan
Write-Host "Wenn Postgres weiterhin nicht geladen werden kann, nutzen Sie die Datei 'docker-compose.sqlite.yml':"
Write-Host "Befehl: docker compose -f docker-compose.sqlite.yml up -d"
