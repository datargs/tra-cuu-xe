@echo off
setlocal

set TASK_NAME=TraCuuXeWeb

echo Removing Windows startup task: %TASK_NAME%
schtasks /Delete /F /TN "%TASK_NAME%"

endlocal
