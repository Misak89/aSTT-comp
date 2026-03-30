@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$ownerPids = @(Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if($ownerPids.Count -eq 0){ Write-Host '[web] stop: nic nebezelo'; exit 0 }; foreach($ownerPid in $ownerPids){ try { Stop-Process -Id $ownerPid -Force -ErrorAction Stop; Write-Host \"[web] stop: ukoncen pid=$ownerPid\" } catch { Write-Host \"[web] stop: nelze ukoncit pid=$ownerPid\" } }"
