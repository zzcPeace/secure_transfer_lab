$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    if ($env:OS -ne "Windows_NT") { throw "Build the Windows executable on Windows." }
    & .\scripts\setup.ps1
    & .\.venv\Scripts\python.exe -m pip install -c requirements.txt -e ".[gui,build]"
    if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed." }
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BuildRoot = Join-Path $ProjectRoot "build/windows-$Stamp"
    $DistRoot = Join-Path $ProjectRoot "dist/windows-$Stamp"
    New-Item -ItemType Directory -Path $BuildRoot, $DistRoot | Out-Null
    & .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --console `
        --name sftlab --paths $ProjectRoot --hidden-import sft.gui `
        --distpath $DistRoot --workpath (Join-Path $BuildRoot "work") `
        --specpath $BuildRoot scripts/frozen-entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $Package = Join-Path $DistRoot "sftlab"
    Copy-Item scripts/windows-package/* $Package
    Copy-Item README.md (Join-Path $Package "README.md")
    & .\.venv\Scripts\python.exe -m pip freeze | Out-File (Join-Path $Package "build-environment.txt") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Could not record build dependencies." }
    $Executable = Join-Path $Package "sftlab.exe"
    & $Executable --help
    if ($LASTEXITCODE -ne 0) { throw "Packaged executable could not start." }
    & $Executable probe-gui
    if ($LASTEXITCODE -ne 0) { throw "Packaged executable could not open a Qt window." }
    $Archive = Join-Path $DistRoot "SecureTransfer-Windows-x64.zip"
    Compress-Archive -Path $Package -DestinationPath $Archive
    Write-Host "Created: $Archive"
    Write-Host "Complete the file-transfer checks in README.md using the extracted ZIP."
} finally { Pop-Location }
