@echo off
setlocal

cd /d "%~dp0"

set HOST=127.0.0.1
set PORT=8501

echo Stopping old web process on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
)

echo Starting TraCuuXe web in background...
start "TraCuuXe Web" /min python -m waitress --listen=%HOST%:%PORT% --threads=8 app:app

timeout /t 5 >nul

tasklist /FI "IMAGENAME eq cloudflared.exe" | find /I "cloudflared.exe" >nul
if errorlevel 1 (
    if exist "C:\Program Files\cloudflared\cloudflared.exe" (
        echo Starting cloudflared tunnel...
        start "TraCuuXe Tunnel" /min "C:\Program Files\cloudflared\cloudflared.exe" tunnel run autocare-local
    ) else if exist "C:\cloudflared\cloudflared.exe" (
        echo Starting cloudflared tunnel...
        start "TraCuuXe Tunnel" /min "C:\cloudflared\cloudflared.exe" tunnel run autocare-local
    ) else (
        echo cloudflared.exe not found. Web is running locally only.
    )
) else (
    echo cloudflared is already running.
)

endlocal
