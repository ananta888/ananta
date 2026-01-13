# Dieses Skript konfiguriert den Windows-Host so, dass Docker-Container auf lokale LLM-Dienste zugreifen können.
# Es muss mit Administratorrechten ausgeführt werden.

function Test-Admin {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $user).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "FEHLER: Dieses Skript muss als ADMINISTRATOR ausgeführt werden." -ForegroundColor Red
    Write-Host "Bitte klicken Sie mit der rechten Maustaste auf das Skript und wählen Sie 'Als Administrator ausführen'."
    Write-Host "`nDrücken Sie eine beliebige Taste zum Beenden..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit
}

Write-Host "--- Ananta Host Service Setup ---" -ForegroundColor Cyan

# 1. Firewall Regeln
Write-Host "`n1. Konfiguriere Windows Firewall..." -ForegroundColor Yellow
$ports = @(1234, 11434)
foreach ($port in $ports) {
    $ruleName = "Ananta LLM Access ($port)"
    if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
        Write-Host "Regel für Port $port existiert bereits. Aktualisiere..." -ForegroundColor Gray
        Set-NetFirewallRule -DisplayName $ruleName -Profile Any -Action Allow
    } else {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any | Out-Null
        Write-Host "Firewall-Regel für Port $port erstellt (alle Profile)." -ForegroundColor Green
    }
}

# 2. IP Helper Service
Write-Host "`n2. Prüfe IP-Hilfsdienst (erforderlich für Portproxy)..." -ForegroundColor Yellow
$iphlp = Get-Service iphlpsvc
if ($iphlp.Status -ne 'Running') {
    Write-Host "Starte IP-Hilfsdienst..." -ForegroundColor Cyan
    Start-Service iphlpsvc
    Set-Service iphlpsvc -StartupType Automatic
}
Write-Host "IP-Hilfsdienst läuft." -ForegroundColor Green

# 3. Portproxy (von 0.0.0.0 auf 127.0.0.1)
Write-Host "`n3. Konfiguriere Port-Proxying (0.0.0.0 -> 127.0.0.1)..." -ForegroundColor Yellow

# Vorher prüfen, ob auf den Ports überhaupt etwas lauscht
function Test-PortListening($port) {
    return (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

foreach ($port in $ports) {
    if (-not (Test-PortListening $port)) {
        Write-Host "WARNUNG: Auf Port $port scheint aktuell KEIN Dienst auf dem Host zu lauschen." -ForegroundColor Yellow
        Write-Host "         Stellen Sie sicher, dass Ollama (11434) oder LMStudio (1234) gestartet sind."
    }
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=127.0.0.1
    Write-Host "Proxy für Port $port eingerichtet." -ForegroundColor Green
}

Write-Host "`n3. Aktuelle Proxy-Konfiguration:" -ForegroundColor Cyan
netsh interface portproxy show all

Write-Host "`n--- Setup abgeschlossen! ---" -ForegroundColor Green
Write-Host "Sie können nun 'docker compose up -d' ausführen."
Write-Host "Stellen Sie sicher, dass Ollama oder LMStudio auf Ihrem Host gestartet sind."
Write-Host "`nDrücken Sie eine beliebige Taste zum Beenden..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
