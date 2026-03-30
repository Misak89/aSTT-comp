@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$health='DOWN'; try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8012/api/health -TimeoutSec 2; if($r.StatusCode -eq 200){$health='UP'} } catch {}; $ownerPid = Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if(-not $ownerPid){$ownerPid='-'}; Write-Host '[web] status'; Write-Host \"  health:  $health\"; Write-Host \"  port8012:$ownerPid\"; Write-Host '  url:     http://127.0.0.1:8012'"
