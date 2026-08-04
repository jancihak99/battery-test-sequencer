' Silent launcher — no console window, only the BTS GUI.
Option Explicit
Dim fso, sh, dir, pythonw, bat
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
pythonw = dir & "\.venv\Scripts\pythonw.exe"
bat = dir & "\Start BTS.bat"

If fso.FileExists(pythonw) Then
  sh.Run """" & pythonw & """ """ & dir & "\main.py""", 0, False
Else
  ' First run: create venv via the .bat (shows console only for setup).
  sh.Run """" & bat & """", 1, False
End If
