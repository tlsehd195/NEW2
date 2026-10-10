' Double-click to start everything with no black windows; closing the app window stops everything (ADR-0057, ADR-0058). Needs Python and Node.js installed.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
If sh.Run("cmd /c where pyw >nul 2>nul", 0, True) = 0 Then
  sh.Run "pyw -3 scripts\launcher.py start", 0, False
ElseIf sh.Run("cmd /c where pythonw >nul 2>nul", 0, True) = 0 Then
  sh.Run "pythonw scripts\launcher.py start", 0, False
Else
  MsgBox "Python is not installed." & vbCrLf & "Install it from https://www.python.org/downloads/ and check Add python.exe to PATH.", 64, "NEW2"
End If
