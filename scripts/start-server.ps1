param([string]$Config = "runtime/server/server.toml")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts/setup.ps1 first." }
    & .venv\Scripts\python.exe -m sft serve --config $Config
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE." }
}
finally { Pop-Location }
