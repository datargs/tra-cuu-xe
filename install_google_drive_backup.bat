@echo off
setlocal

set "TASK_NAME=TraCuuXeGoogleDriveBackup"
set "TASK_RUNNER=%~dp0run_google_drive_backup.bat"

schtasks /Create /F /TN "%TASK_NAME%" /SC DAILY /ST 02:00 /RU SYSTEM /RL HIGHEST /TR "%TASK_RUNNER%"
if errorlevel 1 (
    echo Khong tao duoc lich backup. Hay chay file nay bang quyen Administrator.
    exit /b 1
)

echo Da tao lich %TASK_NAME%, chay hang ngay luc 02:00.
exit /b 0
