@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [smoke] Missing venv python: .venv\Scripts\python.exe
  exit /b 1
)

".venv\Scripts\python.exe" "scripts\smoke_transcribe_13s.py" %*
exit /b %ERRORLEVEL%

