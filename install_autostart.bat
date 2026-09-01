@echo off
setlocal

cd /d "%~dp0"

set TASK_NAME=TraCuuXeWeb
set TASK_SCRIPT=%~dp0start_web_hidden.vbs

echo Installing Windows startup task: %TASK_NAME%
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo Existing task found. Updating it...
)

schtasks /Create /F /TN "%TASK_NAME%" /TR "wscript.exe %TASK_SCRIPT%" /SC ONSTART /RL HIGHEST

echo.
echo Done. The web will start automatically when Windows starts.
echo You can test now with:
echo schtasks /Run /TN "%TASK_NAME%"

endlocal
