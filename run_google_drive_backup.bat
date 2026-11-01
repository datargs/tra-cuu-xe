@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" "%~dp0backup_google_drive.py"
) else (
    python "%~dp0backup_google_drive.py"
)

exit /b %errorlevel%
