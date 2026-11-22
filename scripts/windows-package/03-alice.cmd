@echo off
cd /d "%~dp0"
sftlab.exe gui --profile runtime/clients/alice/client.json
pause
