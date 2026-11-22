param([string]$Profile = "runtime/clients/alice/client.json")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts/setup.ps1 first." }
    & .venv\Scripts\python.exe -m sft gui --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw "Client exited with code $LASTEXITCODE." }
}
finally { Pop-Location }
