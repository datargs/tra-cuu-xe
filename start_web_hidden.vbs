Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

' Chay web bang virtualenv, khong kill nham tien trinh khac.
shell.Run "cmd /c """ & scriptDir & "\start_web_background.bat""", 0, False
