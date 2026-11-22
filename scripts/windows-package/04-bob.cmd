@echo off
cd /d "%~dp0"
sftlab.exe gui --profile runtime/clients/bob/client.json
pause
