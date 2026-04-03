@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [web] chyba: .venv\Scripts\python.exe neexistuje
  exit /b 2
)
call .venv\Scripts\python.exe -X utf8 scripts\webctl.py up-build %*
