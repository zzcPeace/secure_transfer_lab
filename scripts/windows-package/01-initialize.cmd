@echo off
cd /d "%~dp0"
if exist runtime\server\server.toml (
  echo Existing runtime kept.
) else (
  sftlab.exe init-demo --dir runtime --port 18765
  if errorlevel 1 goto end
)
echo Ready. Start 02-server.cmd, then 03-alice.cmd and 04-bob.cmd.
:end
pause
