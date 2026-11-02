param(
    [string]$Distro = "Ubuntu",
    [int[]]$Ports = @(4200, 7900),
    [switch]$RemoveOnly
)

function Test-Admin {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $user).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "FEHLER: Bitte als Administrator ausfuehren." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "FEHLER: wsl.exe nicht gefunden." -ForegroundColor Red
    exit 1
}

Write-Host "--- WSL Localhost Portproxy Setup ---" -ForegroundColor Cyan
Write-Host "Distro: $Distro" -ForegroundColor Gray
Write-Host "Ports : $($Ports -join ', ')" -ForegroundColor Gray

Set-Service iphlpsvc -StartupType Automatic
Start-Service iphlpsvc

$wslIp = (wsl.exe -d $Distro -- sh -lc "ip -o -4 addr show eth0 | awk '{print \$4}' | cut -d/ -f1" 2>$null | Select-Object -First 1).Trim()
if (-not $wslIp) {
    Write-Host "FEHLER: Konnte WSL-IP fuer Distro '$Distro' nicht ermitteln." -ForegroundColor Red
    Write-Host "Tipp: Verfuegbare Distros anzeigen mit: wsl -l -v"
    exit 1
}

Write-Host "WSL-IP: $wslIp" -ForegroundColor Green

foreach ($port in $Ports) {
    netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$port | Out-Null
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port | Out-Null

    if (-not $RemoveOnly) {
        netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$port connectaddress=$wslIp connectport=$port
        Write-Host "OK: localhost:$port -> $wslIp:$port" -ForegroundColor Green
    } else {
        Write-Host "Removed: localhost:$port" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Aktive Regeln (gefiltert):" -ForegroundColor Cyan
netsh interface portproxy show v4tov4 | Select-String -Pattern "127.0.0.1|$wslIp"

if (-not $RemoveOnly) {
    Write-Host ""
    Write-Host "Hinweis: Nach 'wsl --shutdown' kann sich die WSL-IP aendern." -ForegroundColor Yellow
    Write-Host "Dann dieses Skript erneut starten." -ForegroundColor Yellow
}

