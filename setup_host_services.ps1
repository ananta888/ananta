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

# Best-effort: detect WSL/Rancher subnets to allow inbound from VM.
$wslSubnets = @()
try {
    function Get-WslNameserver {
        param([string]$Distro)
        $cmd = "grep -m1 nameserver /etc/resolv.conf | awk '{print $2}'"
        if ($Distro) {
            return (& wsl.exe -d $Distro -- sh -lc $cmd 2>$null | Select-Object -First 1).Trim()
        }
        return (& wsl.exe -- sh -lc $cmd 2>$null | Select-Object -First 1).Trim()
    }

    $distros = & wsl.exe -l -q 2>$null
    foreach ($d in $distros) {
        $d = ($d -replace "`0", "").Trim()
        if (-not $d) { continue }
        $ns = Get-WslNameserver -Distro $d
        if ($ns -match '^(\d+\.\d+\.\d+)\.\d+$') {
            $wslSubnets += "$($Matches[1]).0/24"
        }
    }

    if (-not $wslSubnets) {
        $ns = Get-WslNameserver
        if ($ns -match '^(\d+\.\d+\.\d+)\.\d+$') {
            $wslSubnets += "$($Matches[1]).0/24"
        }
    }
} catch {
    $wslSubnets = @()
}
$wslSubnets = $wslSubnets | Where-Object { $_ } | Select-Object -Unique

# Add WSL/Rancher vEthernet subnets as a fallback.
try {
    $ifAddrs = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like "*WSL*" -or $_.InterfaceAlias -like "*Rancher*" }
    foreach ($addr in $ifAddrs) {
        if ($addr.IPAddress -match '^(\d+\.\d+\.\d+)\.\d+$') {
            $wslSubnets += "$($Matches[1]).0/24"
        }
    }
} catch {
    # Ignore interface detection failures.
}
$wslSubnets = $wslSubnets | Where-Object { $_ } | Select-Object -Unique
$wslSubnets = $wslSubnets | Where-Object { $_ } | Select-Object -Unique

# 1. Firewall Regeln
Write-Host "`n1. Konfiguriere Windows Firewall..." -ForegroundColor Yellow
$ports = @(1234, 11434)
foreach ($port in $ports) {
    $ruleName = "Ananta LLM Access ($port)"
    if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
        Write-Host "Regel für Port $port existiert bereits. Aktualisiere..." -ForegroundColor Gray
        Set-NetFirewallRule -DisplayName $ruleName -Profile Any -Action Allow -EdgeTraversalPolicy Allow
    } else {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any -EdgeTraversalPolicy Allow | Out-Null
        Write-Host "Firewall-Regel für Port $port erstellt (alle Profile)." -ForegroundColor Green
    }
}

# Optional: allow inbound explicitly on WSL/Rancher interfaces.
$wslIfaces = Get-NetAdapter -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -like "*WSL*" -or $_.InterfaceAlias -like "*Rancher*" }
if ($wslIfaces) {
    foreach ($port in $ports) {
        foreach ($iface in $wslIfaces) {
            $ifaceRule = "Ananta LLM Access WSL Interface ($port) $($iface.InterfaceAlias)"
            if (Get-NetFirewallRule -DisplayName $ifaceRule -ErrorAction SilentlyContinue) {
                Remove-NetFirewallRule -DisplayName $ifaceRule | Out-Null
            }
            New-NetFirewallRule -DisplayName $ifaceRule -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any -InterfaceAlias $iface.InterfaceAlias -EdgeTraversalPolicy Allow | Out-Null
            Write-Host "Firewall-Regel fuer Interface erstellt: $($iface.InterfaceAlias) ($port)" -ForegroundColor Green
        }
    }
}

# Optional: allow only WSL/Rancher subnets (more precise than Any).
if ($wslSubnets -and $wslSubnets.Count -gt 0) {
    Write-Host "WSL/Rancher Subnets erkannt: $($wslSubnets -join ', ')" -ForegroundColor Cyan
    foreach ($port in $ports) {
        $ruleName = "Ananta LLM Access WSL ($port)"
        if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $ruleName | Out-Null
        }
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any -EdgeTraversalPolicy Allow -RemoteAddress $wslSubnets -EdgeTraversalPolicy Allow | Out-Null
        Write-Host "Firewall-Regel fuer WSL/Rancher ($port) erstellt: $($wslSubnets -join ', ')" -ForegroundColor Green
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
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $connectAddr = "127.0.0.1"
    
    if ($conns) {
        if ($conns | Where-Object { $_.LocalAddress -in @("0.0.0.0", "127.0.0.1", "::") }) {
            Write-Host "Dienst auf Port $port lauscht auf allen Schnittstellen (0.0.0.0) oder Localhost." -ForegroundColor Gray
        } else {
            $foundAddr = ($conns | Where-Object { $_.LocalAddress -and $_.LocalAddress -notlike "169.254.*" } | Select-Object -First 1).LocalAddress
            if ($foundAddr) {
                $connectAddr = $foundAddr
                Write-Host "Dienst auf Port $port lauscht auf IP: $connectAddr" -ForegroundColor Cyan
            } else {
                Write-Host "Dienst auf Port $port lauscht auf einer unbekannten IP." -ForegroundColor Yellow
            }
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




