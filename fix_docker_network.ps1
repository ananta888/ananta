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

Write-Host "`n4. Teste Container-Internetverbindung..." -ForegroundColor Cyan
try {
    docker run --rm alpine ping -c 4 google.com
    Write-Host "Erfolg: Container haben Internetzugriff!" -ForegroundColor Green
} catch {
    Write-Host "Fehler: Container haben KEINEN Internetzugriff." -ForegroundColor Red
    Write-Host "Dies liegt oft an der DNS-Auflösung in WSL2/Docker."
}

Write-Host "`n5. Manueller Pull-Versuch..." -ForegroundColor Cyan
try {
    docker pull postgres:15-alpine
    Write-Host "Erfolg: Image konnte geladen werden!" -ForegroundColor Green
} catch {
    Write-Host "Fehler beim Pull: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n6. Workaround für das Projekt" -ForegroundColor Cyan
Write-Host "- Wir haben DNS-Server (8.8.8.8) direkt in die docker-compose.yml Dateien eingetragen."
Write-Host "- Falls es immer noch hakt: Nutzen Sie die SQLite-Variante:"
Write-Host "  docker compose -f docker-compose.sqlite.yml up -d"
