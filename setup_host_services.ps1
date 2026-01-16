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

# Best-effort: detect WSL/Rancher subnet to allow inbound from VM.
$wslSubnet = $null
try {
    $wslNameserver = & wsl.exe -e sh -lc "grep -m1 nameserver /etc/resolv.conf | awk '{print $2}'" 2>$null
    if ($wslNameserver -match '^(\d+\.\d+\.\d+)\.\d+$') {
        $wslSubnet = "$($Matches[1]).0/24"
    }
} catch {
    $wslSubnet = $null
}

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

# Optional: allow only WSL/Rancher subnet (more precise than Any).
if ($wslSubnet) {
    Write-Host "WSL/Rancher Subnet erkannt: $wslSubnet" -ForegroundColor Cyan
    foreach ($port in $ports) {
        $ruleName = "Ananta LLM Access WSL ($port)"
        if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $ruleName | Out-Null
        }
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any -RemoteAddress $wslSubnet | Out-Null
        Write-Host "Firewall-Regel fuer WSL/Rancher ($port) erstellt: $wslSubnet" -ForegroundColor Green
    }
} else {
    Write-Host "WARNUNG: Konnte WSL/Rancher Subnet nicht ermitteln." -ForegroundColor Yellow
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

# 3. Portproxy (von Host-IP auf die tatsaechliche IP des Dienstes)
# Dies ist der entscheidende Teil: Selbst wenn LMStudio/Ollama nur auf 127.0.0.1 lauschen, 
# macht dieser Proxy sie für das Docker-Netzwerk (das über das virtuelle Gateway kommt) sichtbar.
Write-Host "`n3. Konfiguriere Port-Proxying (Host-IP -> Ziel-IP)..." -ForegroundColor Yellow
$listenAddress = (Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp,Manual -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -and $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress
if (-not $listenAddress) {
    $listenAddress = "0.0.0.0"
    Write-Host "WARNUNG: Keine geeignete Host-IP gefunden, fallback auf 0.0.0.0 (kann Konflikte verursachen)." -ForegroundColor Yellow
} else {
    Write-Host "Verwende Host-IP fuer Portproxy: $listenAddress" -ForegroundColor Cyan
}


foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $connectAddr = "127.0.0.1"
    
    if ($conn) {
        $foundAddr = $conn.LocalAddress
        # Falls der Dienst auf einer spezifischen IP (wie 192.168...) lauscht, nutzen wir diese
        if ($foundAddr -and $foundAddr -ne "0.0.0.0" -and $foundAddr -ne "::") {
             $connectAddr = $foundAddr
             Write-Host "Dienst auf Port $port lauscht auf IP: $connectAddr" -ForegroundColor Cyan
        } else {
             Write-Host "Dienst auf Port $port lauscht auf allen Schnittstellen (0.0.0.0) oder Localhost." -ForegroundColor Gray
        }
    } else {
        Write-Host "WARNUNG: Auf Port $port scheint aktuell KEIN Dienst auf dem Host zu lauschen." -ForegroundColor Yellow
        Write-Host "         Stellen Sie sicher, dass Ollama (11434) oder LMStudio (1234) gestartet sind."
    }
    
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 | Out-Null
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$connectAddr
    Write-Host "Proxy fuer Port $port eingerichtet: 0.0.0.0 -> $connectAddr" -ForegroundColor Green
}

Write-Host "`n3. Aktuelle Proxy-Konfiguration:" -ForegroundColor Cyan
netsh interface portproxy show all

Write-Host "`n--- Setup abgeschlossen! ---" -ForegroundColor Green
Write-Host "Sie können nun 'docker compose up -d' ausführen."
Write-Host "Stellen Sie sicher, dass Ollama oder LMStudio auf Ihrem Host gestartet sind."
Write-Host "`nDrücken Sie eine beliebige Taste zum Beenden..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
