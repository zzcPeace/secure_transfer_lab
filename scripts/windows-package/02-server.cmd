@echo off
cd /d "%~dp0"
sftlab.exe serve --config runtime/server/server.toml
pause
