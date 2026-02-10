param(
  [ValidateSet("up", "down", "ps", "logs")]
  [string]$Action = "up",
  [switch]$Build
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-PortInUse {
  param([int]$Port)
  $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -eq $Port }
  return ($null -ne $listeners)
}

function Resolve-Port {
  param(
    [int]$Preferred,
    [int]$Fallback
  )
  if (-not (Test-PortInUse -Port $Preferred)) {
    return $Preferred
  }
  if (-not (Test-PortInUse -Port $Fallback)) {
    return $Fallback
  }
  $candidate = $Fallback + 1
  while (Test-PortInUse -Port $candidate) {
    $candidate++
  }
  return $candidate
}

function Convert-ToWslPath {
  param([string]$Path)
  $full = [System.IO.Path]::GetFullPath($Path)
  $drive = $full.Substring(0, 1).ToLowerInvariant()
  $rest = $full.Substring(2).Replace('\', '/')
  return "/mnt/$drive$rest"
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$wslRoot = Convert-ToWslPath -Path $root
$pgPort = Resolve-Port -Preferred 5432 -Fallback 5433
$redisPort = Resolve-Port -Preferred 6379 -Fallback 6380

$envLine = "POSTGRES_PORT=$pgPort REDIS_PORT=$redisPort"
$compose = "docker compose -f docker-compose.base.yml -f docker-compose-lite.yml"

switch ($Action) {
  "down" {
    $cmd = "cd $wslRoot && $envLine $compose down -v --remove-orphans"
  }
  "ps" {
    $cmd = "cd $wslRoot && $envLine $compose ps"
  }
  "logs" {
    $cmd = "cd $wslRoot && $envLine $compose logs --tail=200"
  }
  default {
    $buildArg = ""
    if ($Build) { $buildArg = " --build" }
    $cmd = "cd $wslRoot && $envLine $compose up -d$buildArg"
  }
}

Write-Host "Using WSL path: $wslRoot"
Write-Host "Using POSTGRES_PORT=$pgPort REDIS_PORT=$redisPort"
Write-Host "Action: $Action"
wsl -e sh -lc $cmd
