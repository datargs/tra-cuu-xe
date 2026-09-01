@echo off
setlocal

cd /d "%~dp0"

set HOST=0.0.0.0
set PORT=8501
set LOG_DIR=%~dp0logs
set RESTART_LOG=%LOG_DIR%\restart_web_hidden.log
set WEB_LOG=%LOG_DIR%\web_hidden.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

echo [%date% %time%] Restarting TraCuuXe web on %HOST%:%PORT%>"%RESTART_LOG%"

rmdir /s /q "__pycache__" >nul 2>&1
for /r %%F in (*.pyc) do del /f /q "%%F" >nul 2>&1

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo [%date% %time%] Stopping PID %%P>>"%RESTART_LOG%"
    taskkill /PID %%P /F >>"%RESTART_LOG%" 2>&1
)

echo [%date% %time%] Starting Waitress>>"%RESTART_LOG%"
python -m waitress --listen=%HOST%:%PORT% --threads=8 app:app >>"%WEB_LOG%" 2>&1

endlocal
