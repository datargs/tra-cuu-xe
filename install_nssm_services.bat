@echo off
setlocal

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"
set "NSSM=C:\nssm\nssm.exe"
set "WEB_SERVICE=TraCuuXeWeb"
set "TUNNEL_SERVICE=TraCuuXeTunnel"
set "TUNNEL_NAME=autocare-local"
set "TUNNEL_CONFIG=%USERPROFILE%\.cloudflared\config.yml"
set "HOST=127.0.0.1"
set "PORT=8501"

if not exist "%NSSM%" (
    echo ERROR: NSSM not found at %NSSM%
    exit /b 1
)

set "PYTHON_EXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) do (
    if not defined PYTHON_EXE if exist "%%~P" set "PYTHON_EXE=%%~P"
)

if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
    )
)

if not defined PYTHON_EXE (
    echo ERROR: Python was not found.
    exit /b 1
)

if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"

echo Using Python: %PYTHON_EXE%
echo Stopping any old web process on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
)

echo Installing/updating %WEB_SERVICE%...

sc query "%WEB_SERVICE%" >nul 2>&1
if errorlevel 1 (
    "%NSSM%" install "%WEB_SERVICE%" "%PYTHON_EXE%"
)

"%NSSM%" set "%WEB_SERVICE%" AppDirectory "%APP_DIR%"
"%NSSM%" set "%WEB_SERVICE%" AppParameters -m waitress --listen=%HOST%:%PORT% --threads=8 app:app
"%NSSM%" set "%WEB_SERVICE%" DisplayName "TraCuuXe Web"
"%NSSM%" set "%WEB_SERVICE%" Description "TraCuuXe Flask app served by Waitress"
"%NSSM%" set "%WEB_SERVICE%" Start SERVICE_AUTO_START
"%NSSM%" set "%WEB_SERVICE%" AppStdout "%APP_DIR%\logs\web-out.log"
"%NSSM%" set "%WEB_SERVICE%" AppStderr "%APP_DIR%\logs\web-err.log"
"%NSSM%" set "%WEB_SERVICE%" AppRotateFiles 1
"%NSSM%" set "%WEB_SERVICE%" AppRotateOnline 1
"%NSSM%" set "%WEB_SERVICE%" AppRotateBytes 10485760
"%NSSM%" set "%WEB_SERVICE%" AppThrottle 1500
"%NSSM%" set "%WEB_SERVICE%" AppExit Default Restart

set "CLOUDFLARED_EXE="
if exist "C:\Program Files\cloudflared\cloudflared.exe" set "CLOUDFLARED_EXE=C:\Program Files\cloudflared\cloudflared.exe"
if not defined CLOUDFLARED_EXE if exist "C:\cloudflared\cloudflared.exe" set "CLOUDFLARED_EXE=C:\cloudflared\cloudflared.exe"

if defined CLOUDFLARED_EXE (
    echo Installing/updating %TUNNEL_SERVICE%...
    sc query "%TUNNEL_SERVICE%" >nul 2>&1
    if errorlevel 1 (
        "%NSSM%" install "%TUNNEL_SERVICE%" "%CLOUDFLARED_EXE%"
    )
    "%NSSM%" set "%TUNNEL_SERVICE%" AppDirectory "%APP_DIR%"
    if exist "%TUNNEL_CONFIG%" (
        "%NSSM%" set "%TUNNEL_SERVICE%" AppParameters tunnel --config "%TUNNEL_CONFIG%" run %TUNNEL_NAME%
    ) else (
        "%NSSM%" set "%TUNNEL_SERVICE%" AppParameters tunnel run %TUNNEL_NAME%
    )
    "%NSSM%" set "%TUNNEL_SERVICE%" DisplayName "TraCuuXe Cloudflare Tunnel"
    "%NSSM%" set "%TUNNEL_SERVICE%" Description "Cloudflare tunnel for autocare.ai.vn"
    "%NSSM%" set "%TUNNEL_SERVICE%" Start SERVICE_AUTO_START
    "%NSSM%" set "%TUNNEL_SERVICE%" AppStdout "%APP_DIR%\logs\tunnel-out.log"
    "%NSSM%" set "%TUNNEL_SERVICE%" AppStderr "%APP_DIR%\logs\tunnel-err.log"
    "%NSSM%" set "%TUNNEL_SERVICE%" AppRotateFiles 1
    "%NSSM%" set "%TUNNEL_SERVICE%" AppRotateOnline 1
    "%NSSM%" set "%TUNNEL_SERVICE%" AppRotateBytes 10485760
    "%NSSM%" set "%TUNNEL_SERVICE%" AppThrottle 1500
    "%NSSM%" set "%TUNNEL_SERVICE%" AppExit Default Restart
) else (
    echo cloudflared.exe not found. Skipping tunnel service.
)

echo Starting services...
"%NSSM%" restart "%WEB_SERVICE%"
if errorlevel 1 "%NSSM%" start "%WEB_SERVICE%"
if defined CLOUDFLARED_EXE (
    "%NSSM%" restart "%TUNNEL_SERVICE%"
    if errorlevel 1 "%NSSM%" start "%TUNNEL_SERVICE%"
)

echo.
echo Done. Check:
echo   sc query %WEB_SERVICE%
echo   sc query %TUNNEL_SERVICE%
echo   http://127.0.0.1:%PORT%/login

endlocal
