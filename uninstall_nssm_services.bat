@echo off
setlocal

set "NSSM=C:\nssm\nssm.exe"

if not exist "%NSSM%" (
    echo ERROR: NSSM not found at %NSSM%
    exit /b 1
)

"%NSSM%" stop TraCuuXeWeb
"%NSSM%" remove TraCuuXeWeb confirm

"%NSSM%" stop TraCuuXeTunnel
"%NSSM%" remove TraCuuXeTunnel confirm

echo Done.
endlocal
