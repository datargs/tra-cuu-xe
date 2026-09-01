@echo off
setlocal

cd /d "%~dp0"

set HOST=127.0.0.1
set PORT=8501

echo Stopping old web process on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
)

echo Starting web at http://%HOST%:%PORT%
python -m waitress --listen=%HOST%:%PORT% --threads=8 app:app

endlocal
