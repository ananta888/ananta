param(
    [string]$InstallDir = "$HOME\ananta",
    [string]$Ref = "main",
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Resolve-PythonCommand {
    if (Test-Command "python") { return "python" }
    if (Test-Command "py") { return "py" }
    throw "Python is required. Install from https://www.python.org/downloads/ or run: winget install Python.Python.3.12"
}

function Resolve-VenvPython {
    param([string]$BasePath)
    $scriptsPython = Join-Path $BasePath ".venv\Scripts\python.exe"
    if (Test-Path $scriptsPython) { return $scriptsPython }
    $binPython = Join-Path $BasePath ".venv\bin\python"
    if (Test-Path $binPython) { return $binPython }
    return $null
}

if (-not (Test-Command "git")) {
    throw "git is required. Install from https://git-scm.com/downloads or run: winget install Git.Git"
}

$pythonCmd = Resolve-PythonCommand
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
$parentDir = Split-Path -Parent $InstallDir
if (-not (Test-Path $parentDir)) {
    New-Item -ItemType Directory -Path $parentDir | Out-Null
}

if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
    if ((Test-Path $InstallDir) -and ((Get-ChildItem -Force $InstallDir | Measure-Object).Count -gt 0)) {
        throw "Install dir exists and is not an Ananta git checkout: $InstallDir"
    }
    Write-Host "Cloning Ananta into $InstallDir ..."
    git clone --branch $Ref --single-branch https://github.com/ananta888/ananta.git $InstallDir
}

Push-Location $InstallDir
try {
    $dirty = (git status --porcelain)
    if ($dirty -and (-not $AllowDirty)) {
        throw "Existing checkout is dirty. Commit/stash changes or rerun with -AllowDirty."
    }

    $venvPython = Resolve-VenvPython -BasePath $InstallDir
    if ($venvPython) {
        $updateArgs = @("-m", "agent.cli.main", "update", "--repo-dir", $InstallDir, "--ref", $Ref)
        if ($AllowDirty) {
            $updateArgs += "--allow-dirty"
        }
        Write-Host "Running unified update path: $venvPython $($updateArgs -join ' ')"
        try {
            & $venvPython @updateArgs
            if ($LASTEXITCODE -ne 0) {
                throw "Unified update returned exit code $LASTEXITCODE"
            }
        } catch {
            Write-Warning "Unified update failed, falling back to installer update flow."
            git fetch --tags --prune origin
            git checkout $Ref
            $branch = (git rev-parse --abbrev-ref HEAD).Trim()
            if ($branch -ne "HEAD") {
                git pull --ff-only origin $branch
            }
        }
    } else {
        git fetch --tags --prune origin
        git checkout $Ref
        $branch = (git rev-parse --abbrev-ref HEAD).Trim()
        if ($branch -ne "HEAD") {
            git pull --ff-only origin $branch
        }
    }

    if (-not (Test-Path ".venv")) {
        & $pythonCmd -m venv .venv
    }

    $venvPython = Resolve-VenvPython -BasePath $InstallDir
    if (-not $venvPython) {
        throw "Virtualenv python not found in $InstallDir\.venv"
    }

    & $venvPython -m pip install --upgrade pip
    if (Test-Path "requirements.lock") {
        & $venvPython -m pip install -r requirements.lock
    } elseif (Test-Path "requirements.txt") {
        & $venvPython -m pip install -r requirements.txt
    }
    if (Test-Path "requirements-dev.lock") {
        & $venvPython -m pip install -r requirements-dev.lock
    }
    & $venvPython -m pip install -e .
    & $venvPython -m agent.cli.main --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Post-install smoke check failed."
    }

    Write-Host ""
    Write-Host "Ananta installation completed."
    Write-Host "Install dir: $InstallDir"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  $venvPython -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default"
    Write-Host "  $venvPython -m agent.cli.main doctor"
    Write-Host "  $venvPython -m agent.cli.main status"
    Write-Host ""
    Write-Host "Runtime examples:"
    Write-Host "  Local Ollama:"
    Write-Host "    $venvPython -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default"
    Write-Host "  OpenAI-compatible:"
    Write-Host "    $venvPython -m agent.cli.main init --yes --runtime-mode local-dev --llm-backend openai-compatible --endpoint-url http://localhost:1234/v1 --model your-model"
    Write-Host ""
    Write-Host "Note: this installer does not store API keys; configure provider credentials in your shell/profile."
} finally {
    Pop-Location
}
