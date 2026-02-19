param(
  [switch]$Persist
)

$ErrorActionPreference = "Stop"

Write-Host "Setting vm.overcommit_memory=1 in docker-desktop WSL VM..."
wsl -d docker-desktop sysctl -w vm.overcommit_memory=1 | Out-Host

if ($Persist) {
  Write-Host "Persisting vm.overcommit_memory=1 in /etc/sysctl.conf..."
  wsl -d docker-desktop sh -c "grep -q '^vm.overcommit_memory=' /etc/sysctl.conf && sed -i 's/^vm.overcommit_memory=.*/vm.overcommit_memory=1/' /etc/sysctl.conf || echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf"
  wsl -d docker-desktop sysctl -p | Out-Host
}

Write-Host "Done."

