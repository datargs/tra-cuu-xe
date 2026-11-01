@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0logs" mkdir "%~dp0logs" >nul 2>&1
call "%~dp0start_web_background.bat" > "%~dp0logs\restart_web_hidden.log" 2>&1
endlocal
