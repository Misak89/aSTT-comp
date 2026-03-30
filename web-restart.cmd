@echo off
setlocal
cd /d "%~dp0"
call "%~dp0web-down.cmd"
call "%~dp0web-up.cmd"
