# Dieses Skript konfiguriert den Windows-Host so, dass Docker-Container auf lokale LLM-Dienste zugreifen können.
# Es muss mit Administratorrechten ausgeführt werden.

function Test-Admin {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $user).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Normalize-LmStudioBaseUrl {
    param([string]$Url)

    $raw = "$Url".Trim()
    if (-not $raw) { return $null }

    $normalized = $raw.TrimEnd('/')
    foreach ($suffix in @('/chat/completions', '/completions', '/responses', '/models')) {
        if ($normalized.ToLower().EndsWith($suffix)) {
            $normalized = $normalized.Substring(0, $normalized.Length - $suffix.Length)
            break
        }
    }

    try {
        $uri = [System.Uri]$normalized
    } catch {
        return $null
    }

    if (-not $uri.Scheme -or -not $uri.Host) {
        return $null
    }

    $path = $uri.AbsolutePath.TrimEnd('/')
    $pathLower = $path.ToLower()
    if ($pathLower.EndsWith('/v1')) {
        $resolvedPath = $path
    } elseif ($pathLower.Contains('/v1')) {
        $idx = $pathLower.IndexOf('/v1')
        $resolvedPath = $path.Substring(0, $idx + 3)
    } elseif (-not $path) {
        $resolvedPath = '/v1'
    } else {
        $resolvedPath = "$path/v1"
    }

    return "{0}://{1}{2}" -f $uri.Scheme, $uri.Authority, $resolvedPath
}

function Test-HttpJsonEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 3
    )

    $result = @{
        ok = $false
        status_code = $null
        error = $null
        json = $null
    }

    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing -ErrorAction Stop
        $result.ok = $true
        $result.status_code = [int]$response.StatusCode
        if ($response.Content) {
            try {
                $result.json = $response.Content | ConvertFrom-Json -ErrorAction Stop
            } catch {
                $result.json = $null
            }
        }
    } catch {
        $result.error = $_.Exception.Message
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $result.status_code = [int]$_.Exception.Response.StatusCode
        }
    }

    return $result
}

function Find-BackendCommand {
    param([string[]]$Names)

    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    return $null
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
$ports = @(1234, 11434, 5000, 5001, 5002, 5003, 5004, 5005, 4200)
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
    
    # Entferne ggf. alte Regeln (0.0.0.0 blockiert lokale Dienste wie LM Studio)
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 | Out-Null
    if ($listenAddress -ne "0.0.0.0") {
        netsh interface portproxy delete v4tov4 listenport=$port listenaddress=$listenAddress | Out-Null
    }
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=$listenAddress connectport=$port connectaddress=$connectAddr
    Write-Host "Proxy fuer Port $port eingerichtet: $listenAddress -> $connectAddr" -ForegroundColor Green
}

# 4. Redis Optimierungen (WSL2 / Docker Desktop)
Write-Host "`n4. Prüfe Redis Optimierungen (WSL2 / Docker Desktop)..." -ForegroundColor Yellow
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    try {
        Write-Host "Setze vm.overcommit_memory = 1 in WSL2..." -ForegroundColor Cyan
        & wsl.exe -u root sh -c "echo 1 > /proc/sys/vm/overcommit_memory" 2>$null
        Write-Host "Setze vm.overcommit_memory = 1 in docker-desktop..." -ForegroundColor Cyan
        & wsl.exe -d docker-desktop sh -c "echo 1 > /proc/sys/vm/overcommit_memory" 2>$null
        & wsl.exe -d docker-desktop sh -c "grep -q '^vm.overcommit_memory=' /etc/sysctl.conf && sed -i 's/^vm.overcommit_memory=.*/vm.overcommit_memory=1/' /etc/sysctl.conf || echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf" 2>$null
        Write-Host "Redis Optimierung angewendet (inkl. persistenter docker-desktop Konfiguration)." -ForegroundColor Green
    } catch {
        Write-Host "WARNUNG: Konnte Redis Optimierung in WSL2 nicht automatisch anwenden." -ForegroundColor Yellow
    }
} else {
    Write-Host "WSL nicht gefunden. Überspringe Redis Optimierung." -ForegroundColor Gray
}

# 5. Lokaler Backend-Preflight
Write-Host "`n5. Lokaler Backend-Preflight..." -ForegroundColor Yellow

$lmStudioBaseUrl = Normalize-LmStudioBaseUrl "http://127.0.0.1:1234/v1"
$lmStudioModelsUrl = if ($lmStudioBaseUrl) { "$lmStudioBaseUrl/models" } else { $null }

if ($lmStudioModelsUrl) {
    $lmStudioProbe = Test-HttpJsonEndpoint -Url $lmStudioModelsUrl -TimeoutSeconds 3
    if ($lmStudioProbe.ok) {
        $candidateCount = 0
        if ($lmStudioProbe.json -and $lmStudioProbe.json.data) {
            $candidateCount = @($lmStudioProbe.json.data | Where-Object {
                $_ -and $_.id -and -not ("$($_.id)".ToLower().Contains("embed"))
            }).Count
        }
        Write-Host "LM Studio erreichbar: $lmStudioModelsUrl (candidate_count=$candidateCount)" -ForegroundColor Green
        if ($candidateCount -eq 0) {
            Write-Host "WARNUNG: LM Studio antwortet, aber es scheint kein Chat-Modell geladen zu sein." -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARNUNG: LM Studio /v1/models nicht erreichbar: $lmStudioModelsUrl" -ForegroundColor Yellow
        if ($lmStudioProbe.error) {
            Write-Host "         Fehler: $($lmStudioProbe.error)" -ForegroundColor Yellow
        }
    }
}

$agentChecks = @(
    @{ Name = "Hub"; Url = "http://127.0.0.1:5000/health" },
    @{ Name = "Worker 5001"; Url = "http://127.0.0.1:5001/health" },
    @{ Name = "Worker 5002"; Url = "http://127.0.0.1:5002/health" }
)
foreach ($entry in $agentChecks) {
    $probe = Test-HttpJsonEndpoint -Url $entry.Url -TimeoutSeconds 2
    if ($probe.ok) {
        Write-Host "$($entry.Name) Health OK: $($entry.Url)" -ForegroundColor Green
    } else {
        Write-Host "HINWEIS: Kein Health-Response von $($entry.Name): $($entry.Url)" -ForegroundColor Gray
    }
}

$cliChecks = @(
    @{ Name = "codex"; Commands = @("codex") ; Hint = "npm i -g @openai/codex" },
    @{ Name = "opencode"; Commands = @("opencode") ; Hint = "npm i -g opencode-ai" },
    @{ Name = "aider"; Commands = @("aider", "aider-chat") ; Hint = "python -m pip install aider-chat" },
    @{ Name = "mistral_code"; Commands = @("mistral-code") ; Hint = "npm i -g mistral-code" }
)
foreach ($cli in $cliChecks) {
    $resolved = Find-BackendCommand -Names $cli.Commands
    if ($resolved) {
        Write-Host "CLI gefunden [$($cli.Name)]: $resolved" -ForegroundColor Green
    } else {
        Write-Host "CLI fehlt [$($cli.Name)] - Install: $($cli.Hint)" -ForegroundColor Yellow
    }
}

Write-Host "`n6. Aktuelle Proxy-Konfiguration:" -ForegroundColor Cyan
netsh interface portproxy show all

Write-Host "`n--- Setup abgeschlossen! ---" -ForegroundColor Green
Write-Host "Sie können nun 'docker compose up -d' ausführen."
Write-Host "Stellen Sie sicher, dass Ollama oder LMStudio auf Ihrem Host gestartet sind."
Write-Host "`nDrücken Sie eine beliebige Taste zum Beenden..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")




