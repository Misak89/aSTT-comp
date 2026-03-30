@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command ^
  "$repo = (Get-Location).Path; " ^
  "$out = Join-Path $repo 'runtime\logs\web-up-bg.out.log'; " ^
  "$err = Join-Path $repo 'runtime\logs\web-up-bg.err.log'; " ^
  "if (Test-Path $out) { Remove-Item -LiteralPath $out -Force }; " ^
  "if (Test-Path $err) { Remove-Item -LiteralPath $err -Force }; " ^
  "Start-Process -FilePath 'cmd.exe' -WorkingDirectory $repo -ArgumentList '/c','call start_web_app.cmd -SkipFrontendBuild' -RedirectStandardOutput $out -RedirectStandardError $err; " ^
  "$ok=$false; for($i=0;$i -lt 40;$i++){ Start-Sleep -Milliseconds 500; try { $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8012/api/health -TimeoutSec 2; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {} }; " ^
  "if($ok){ Write-Host '[web] start-bg: OK http://127.0.0.1:8012' } else { Write-Host '[web] start-bg: backend zatim neodpovida (viz runtime/logs/web-up-bg.*.log)' }"
