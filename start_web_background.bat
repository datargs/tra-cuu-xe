@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "RUNTIME_DIR=%~dp0runtime"
set "PID_FILE=%RUNTIME_DIR%\web.pid"
set "WEB_RUNNER=%~dp0run_web_process.ps1"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_web_process.ps1"
if exist "%PID_FILE%" (
    for /f "usebackq delims=" %%P in ("%PID_FILE%") do taskkill /PID %%P /T /F >nul 2>&1
    del /f /q "%PID_FILE%" >nul 2>&1
)

if not exist "%~dp0venv\Scripts\python.exe" (
    echo ERROR: Virtual environment Python was not found.
    exit /b 1
)

echo Starting TraCuuXe web in background...
start "TraCuuXe Web" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WEB_RUNNER%"
powershell.exe -NoProfile -Command "Start-Sleep -Seconds 5"

tasklist /FI "IMAGENAME eq cloudflared.exe" | find /I "cloudflared.exe" >nul
if errorlevel 1 (
    set "TUNNEL_CONFIG=C:\Users\windows\.cloudflared\config.yml"
    if exist "C:\Program Files\cloudflared\cloudflared.exe" (
        echo Starting cloudflared tunnel...
        if exist "!TUNNEL_CONFIG!" (start "TraCuuXe Tunnel" /min "C:\Program Files\cloudflared\cloudflared.exe" tunnel --config "!TUNNEL_CONFIG!" run autocare-local) else (start "TraCuuXe Tunnel" /min "C:\Program Files\cloudflared\cloudflared.exe" tunnel run autocare-local)
    ) else if exist "C:\cloudflared\cloudflared.exe" (
        echo Starting cloudflared tunnel...
        if exist "!TUNNEL_CONFIG!" (start "TraCuuXe Tunnel" /min "C:\cloudflared\cloudflared.exe" tunnel --config "!TUNNEL_CONFIG!" run autocare-local) else (start "TraCuuXe Tunnel" /min "C:\cloudflared\cloudflared.exe" tunnel run autocare-local)
    ) else (
        echo cloudflared.exe not found. Web is running locally only.
    )
) else (
    echo cloudflared is already running.
)

endlocal
