Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

' Xoa cache tam
shell.Run "cmd /c if exist __pycache__ rmdir /s /q __pycache__", 0, True
shell.Run "cmd /c for /r %F in (*.pyc) do del /f /q ""%F"" >nul 2>&1", 0, True

' Kill process dang chiem cong 8501
shell.Run "cmd /c for /f ""tokens=5"" %P in ('netstat -ano ^| findstr /R /C:"":8501 .*LISTENING""') do taskkill /PID %P /F >nul 2>&1", 0, True

' Bat web an cong so, khong hien cmd
shell.Run "cmd /c start """" /b python -m waitress --listen=0.0.0.0:8501 --threads=8 app:app", 0, False
