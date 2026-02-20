# Ananta Setup Script
# Automatisiert die initiale Einrichtung des Projekts

Write-Host "=== Ananta Setup Script ===" -ForegroundColor Cyan
Write-Host ""

# Function to generate secure random string
function New-SecureToken {
    param([int]$Length = 32)
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    return [Convert]::ToBase64String($bytes) -replace '[/+=]', ''
}

# Function to check if command exists
function Test-Command {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

# 1. Check Dependencies
Write-Host "1. Prüfe Dependencies..." -ForegroundColor Yellow
$missingDeps = @()

if (-not (Test-Command "python")) {
    $missingDeps += "Python (https://www.python.org/downloads/)"
} else {
    $pythonVersion = (python --version 2>&1).ToString()
    Write-Host "  ✓ $pythonVersion gefunden" -ForegroundColor Green
}

if (-not (Test-Command "node")) {
    $missingDeps += "Node.js (https://nodejs.org/)"
} else {
    $nodeVersion = (node --version 2>&1).ToString()
    Write-Host "  ✓ Node.js $nodeVersion gefunden" -ForegroundColor Green
}

if (-not (Test-Command "docker")) {
    $missingDeps += "Docker (https://www.docker.com/products/docker-desktop/)"
} else {
    $dockerVersion = (docker --version 2>&1).ToString()
    Write-Host "  ✓ $dockerVersion gefunden" -ForegroundColor Green
}

if ($missingDeps.Count -gt 0) {
    Write-Host ""
    Write-Host "FEHLER: Folgende Dependencies fehlen:" -ForegroundColor Red
    foreach ($dep in $missingDeps) {
        Write-Host "  - $dep" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "Bitte installieren Sie die fehlenden Dependencies und führen Sie das Script erneut aus."
    exit 1
}

Write-Host ""

# 2. Generate .env file
Write-Host "2. Generiere .env Datei..." -ForegroundColor Yellow

if (Test-Path ".env") {
    Write-Host "  .env existiert bereits. Überspringe Generierung." -ForegroundColor Gray
    Write-Host "  (Löschen Sie .env und führen Sie das Script erneut aus, um neu zu generieren)" -ForegroundColor Gray
} else {
    if (-not (Test-Path ".env.example")) {
        Write-Host "  FEHLER: .env.example nicht gefunden!" -ForegroundColor Red
        exit 1
    }

    Write-Host "  Generiere sichere Passwörter und Tokens..." -ForegroundColor Cyan

    # Read template
    $envContent = Get-Content ".env.example" -Raw

    # Generate secure values
    $postgresPassword = New-SecureToken -Length 24
    $grafanaPassword = New-SecureToken -Length 24
    $adminPassword = New-SecureToken -Length 24
    $hubToken = New-SecureToken -Length 32
    $alphaToken = New-SecureToken -Length 32
    $betaToken = New-SecureToken -Length 32

    # Replace placeholders
    $envContent = $envContent -replace 'replace_this_with_a_secure_password_123!', $postgresPassword
    $envContent = $envContent -replace 'replace_this_with_a_secure_password_456!', $grafanaPassword
    $envContent = $envContent -replace 'replace_this_with_a_secure_admin_password_789!', $adminPassword
    $envContent = $envContent -replace 'generate_a_random_token_for_hub', $hubToken
    $envContent = $envContent -replace 'generate_a_random_token_for_alpha', $alphaToken
    $envContent = $envContent -replace 'generate_a_random_token_for_beta', $betaToken

    # Write .env file
    $envContent | Set-Content ".env" -NoNewline

    Write-Host "  ✓ .env Datei erstellt mit sicheren Passwörtern" -ForegroundColor Green
    Write-Host ""
    Write-Host "  WICHTIG: Notieren Sie das Admin-Passwort:" -ForegroundColor Yellow
    Write-Host "  Username: admin" -ForegroundColor Cyan
    Write-Host "  Password: $adminPassword" -ForegroundColor Cyan
    Write-Host ""
}

Write-Host ""

# 3. Install Python Dependencies
Write-Host "3. Installiere Python Dependencies..." -ForegroundColor Yellow

if (Test-Path "requirements.txt") {
    Write-Host "  Führe pip install aus..." -ForegroundColor Cyan
    try {
        python -m pip install --upgrade pip --quiet
        python -m pip install -r requirements.txt --quiet
        Write-Host "  ✓ Python Dependencies installiert" -ForegroundColor Green
    } catch {
        Write-Host "  WARNUNG: Fehler bei pip install. Bitte manuell ausführen: pip install -r requirements.txt" -ForegroundColor Yellow
    }
} else {
    Write-Host "  requirements.txt nicht gefunden. Überspringe." -ForegroundColor Gray
}

Write-Host ""

# 4. Install Frontend Dependencies
Write-Host "4. Installiere Frontend Dependencies..." -ForegroundColor Yellow

if (Test-Path "frontend-angular\package.json") {
    Push-Location "frontend-angular"
    Write-Host "  Führe npm install aus..." -ForegroundColor Cyan
    try {
        npm install --silent 2>&1 | Out-Null
        Write-Host "  ✓ Frontend Dependencies installiert" -ForegroundColor Green
    } catch {
        Write-Host "  WARNUNG: Fehler bei npm install. Bitte manuell ausführen: cd frontend-angular && npm install" -ForegroundColor Yellow
    }
    Pop-Location
} else {
    Write-Host "  frontend-angular/package.json nicht gefunden. Überspringe." -ForegroundColor Gray
}

Write-Host ""

# 5. Summary and Next Steps
Write-Host "=== Setup abgeschlossen! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Nächste Schritte:" -ForegroundColor Cyan
Write-Host "  1. Starten Sie Ollama oder LMStudio auf Ihrem Host" -ForegroundColor White
Write-Host "  2. (Optional) Führen Sie setup_host_services.ps1 als Administrator aus" -ForegroundColor White
Write-Host "     für Windows-Host-Konfiguration (Firewall, Portproxy)" -ForegroundColor Gray
Write-Host "  3. Starten Sie die Services:" -ForegroundColor White
Write-Host "     docker compose up -d" -ForegroundColor Cyan
Write-Host "  4. Öffnen Sie http://localhost:4200 im Browser" -ForegroundColor White
Write-Host "  5. Login mit:" -ForegroundColor White
Write-Host "     Username: admin" -ForegroundColor Cyan
Write-Host "     Password: (siehe oben)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Weitere Informationen finden Sie in README.md" -ForegroundColor Gray
Write-Host ""
