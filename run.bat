@echo off
setlocal
cd /d "%~dp0"
set ACTION=%~1
if "%ACTION%"=="" goto usage
if "%ACTION%"=="start" goto start
if "%ACTION%"=="status" goto status
if "%ACTION%"=="stop" goto stop
goto usage

:start
where py >nul 2>nul
if errorlevel 1 (set PY=python) else (set PY=py -3)
if not exist .venv\Scripts\python.exe %PY% -m venv .venv
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
if not exist logs mkdir logs
powershell -NoProfile -Command "$p=Start-Process -FilePath '%CD%\\.venv\\Scripts\\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8780' -WorkingDirectory '%CD%' -RedirectStandardOutput '%CD%\\logs\\launcher.log' -RedirectStandardError '%CD%\\logs\\launcher-error.log' -PassThru -WindowStyle Hidden; Set-Content -Path '%CD%\\.launcher.pid' -Value $p.Id"
for /l %%i in (1,1,40) do (
  powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8780/api/config -TimeoutSec 1).StatusCode } catch { 0 }" | findstr "200" >nul && goto ready
  timeout /t 1 /nobreak >nul
)
echo Launcher failed to start. Check logs\launcher-error.log
exit /b 1
:ready
echo Osa Ora App Store started: http://127.0.0.1:8780
start "Osa Ora App Store" http://127.0.0.1:8780
exit /b 0

:status
powershell -NoProfile -Command "if (Test-Path '.launcher.pid') { $p=Get-Content '.launcher.pid'; if (Get-Process -Id $p -ErrorAction SilentlyContinue) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8780/api/config -TimeoutSec 2 | Out-Null; Write-Output 'Osa Ora App Store is running'; exit 0 } catch {} } }; Write-Output 'Osa Ora App Store is stopped'; exit 1"
exit /b %errorlevel%

:stop
if exist .launcher.pid (
  powershell -NoProfile -Command "$p=Get-Content '.launcher.pid'; Stop-Process -Id $p -Force -ErrorAction SilentlyContinue"
  del .launcher.pid
)
echo Osa Ora App Store stopped
exit /b 0

:usage
echo Usage: run.bat ^<start^|stop^|status^>
exit /b 2
