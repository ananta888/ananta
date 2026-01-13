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
    Write-Host "Teste IP-Konnektivität (Ping 8.8.8.8)..."
    docker run --rm alpine ping -c 2 8.8.8.8
    Write-Host "Erfolg: IP-Ebene ok." -ForegroundColor Green
    
    Write-Host "Teste DNS-Auflösung (nslookup google.com)..."
    docker run --rm alpine nslookup google.com
    Write-Host "Erfolg: DNS-Ebene ok." -ForegroundColor Green

    Write-Host "Teste MTU / Paketfragmentierung (Ping mit 1472 Bytes)..."
    # Wenn dies fehlschlägt, aber der normale Ping klappt, liegt ein MTU-Problem vor (oft bei VPNs).
    docker run --rm alpine ping -c 2 -s 1472 google.com
    Write-Host "Erfolg: MTU scheint ok zu sein." -ForegroundColor Green
} catch {
    Write-Host "Fehler: Container haben KEINEN Internetzugriff oder eingeschränkte Konnektivität." -ForegroundColor Red
    Write-Host "Mögliche Ursachen:"
    Write-Host "1. VPN: Schalten Sie Ihr VPN testweise aus."
    Write-Host "2. Firewall: Prüfen Sie, ob eine Firewall (z.B. Bitdefender, Sophos) Docker blockiert."
    Write-Host "3. MTU: Falls normale Pings gehen, aber 'pip install' hakt, setzen Sie die MTU in Docker auf 1400."
    Write-Host "4. IPv6: Deaktivieren Sie IPv6 in den Docker Desktop Einstellungen."
}

Write-Host "`n5. Teste Erreichbarkeit der LLM-Dienste..." -ForegroundColor Cyan
$testUrls = @("http://host.docker.internal:11434/api/generate", "http://host.docker.internal:1234/v1/completions", "http://192.168.56.1:11434/api/generate", "http://192.168.56.1:1234/v1/completions")

foreach ($url in $testUrls) {
    Write-Host "Teste $url..." -NoNewline
    try {
        # Wir nutzen curl im Container um die Verbindung zu testen
        $res = docker run --rm curlimages/curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 $url
        if ($res -eq "200" -or $res -eq "405" -or $res -eq "401" -or $res -eq "404") {
             Write-Host " ERREICHBAR (Status: $res)" -ForegroundColor Green
        } else {
             Write-Host " NICHT ERREICHBAR (Status: $res)" -ForegroundColor Red
        }
    } catch {
        Write-Host " FEHLER (Dienst antwortet nicht)" -ForegroundColor Red
    }
}

Write-Host "`n6. Firewall-Fix (nur falls oben alles 'NICHT ERREICHBAR' ist)" -ForegroundColor Yellow
Write-Host "Führen Sie dies in einer Admin-PowerShell aus:"
Write-Host 'New-NetFirewallRule -DisplayName "Ananta LLM Access" -Direction Inbound -LocalPort 1234,11434 -Protocol TCP -Action Allow'

Write-Host "`n7. Workaround für das Projekt" -ForegroundColor Cyan
Write-Host "- Wir haben DNS-Server (8.8.8.8) in die docker-compose.yml Dateien eingetragen."
Write-Host "- WICHTIG: Falls Sie 'Temporary failure in name resolution' sehen, kommentieren Sie"
Write-Host "  die 'dns:'-Zeilen in der docker-compose.yml aus. In manchen Netzwerken blockieren"
Write-Host "  feste DNS-Einträge die Auflösung über den Host."
Write-Host "- Falls es immer noch hakt: Nutzen Sie die SQLite-Variante:"
Write-Host "  docker compose -f docker-compose.sqlite.yml up -d"
