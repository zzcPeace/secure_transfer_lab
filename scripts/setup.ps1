param([int]$Port = 18765, [string]$PythonExecutable = "")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCheck = @'
import struct
import sys
from pathlib import Path

if sys.version_info < (3, 12) or struct.calcsize('P') != 8:
    sys.exit('Python 3.12+ (64-bit) is required.')
if (Path(sys.base_prefix) / 'conda-meta').is_dir() or 'conda' in sys.version.lower():
    sys.exit('This Python is based on Conda/Anaconda. For this Windows setup, use standalone Python from python.org; see README.md.')
'@
Push-Location $ProjectRoot
try {
    if ($env:OS -ne "Windows_NT") { throw "Use scripts/setup.sh on Linux." }
    if ((Test-Path ".venv") -and -not (Test-Path ".venv\Scripts\python.exe")) {
        throw "This .venv is not a Windows environment. Copy source into a fresh folder without Linux .venv/runtime."
    }
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        $PythonArguments = @()
        if ($PythonExecutable) {
            & $PythonExecutable -I -c $PythonCheck
            if ($LASTEXITCODE -ne 0) { throw "The specified Python is not suitable. See README.md." }
        } else {
            foreach ($Name in @("python", "py")) {
                foreach ($Candidate in @(Get-Command $Name -All -CommandType Application -ErrorAction SilentlyContinue)) {
                    $PythonArguments = @()
                    if ($Name -eq "py") { $PythonArguments = @("-3") }
                    try {
                        & $Candidate.Source @PythonArguments -I -c $PythonCheck 2>$null
                        if ($LASTEXITCODE -eq 0) {
                            $PythonExecutable = $Candidate.Source
                            break
                        }
                    } catch { continue }
                }
                if ($PythonExecutable) { break }
            }
        }
        if (-not $PythonExecutable) {
            throw "Standalone Python 3.12+ (64-bit) was not found. Install it from python.org, then use -PythonExecutable with its full python.exe path. Conda interpreters are skipped. See README.md."
        }
        & $PythonExecutable @PythonArguments -I -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed" }
    }
    & .venv\Scripts\python.exe -I -c $PythonCheck
    if ($LASTEXITCODE -ne 0) { throw "The existing .venv uses an unsuitable base Python. Stop this project's processes, rename .venv as a backup, and create a new environment with system Python as described in README.md. Keep runtime." }
    & .venv\Scripts\python.exe -m pip install -c requirements.txt -e ".[gui]"
    if ($LASTEXITCODE -ne 0) { throw "Project installation failed" }
    if (-not (Test-Path "runtime")) {
        & .venv\Scripts\python.exe -m sft init-demo --dir runtime --port $Port
        if ($LASTEXITCODE -ne 0) { throw "Demo initialization failed" }
    }
    & .venv\Scripts\python.exe -m sft probe-gui
    if ($LASTEXITCODE -ne 0) {
        throw "Qt window check failed. For DLL load errors, install/update the Microsoft Visual C++ x64 Redistributable. See README.md."
    }
    Write-Host "Ready. Run scripts/start-server.ps1 and scripts/start-client.ps1 in separate terminals."
} finally { Pop-Location }
