' Double-click to stop everything. The paper trader saves its state first (takes a few seconds).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
If sh.Run("cmd /c where pyw >nul 2>nul", 0, True) = 0 Then
  sh.Run "pyw -3 scripts\launcher.py stop", 0, True
ElseIf sh.Run("cmd /c where pythonw >nul 2>nul", 0, True) = 0 Then
  sh.Run "pythonw scripts\launcher.py stop", 0, True
End If
MsgBox "Stopped.", 64, "NEW2"
