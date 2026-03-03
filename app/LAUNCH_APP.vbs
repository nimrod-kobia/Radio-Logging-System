Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

' Script lives in app\ subfolder - go up one level to reach the project root
strDir = oFSO.GetParentFolderName(oFSO.GetParentFolderName(WScript.ScriptFullName)) & "\"

' Ensure required folders exist
If Not oFSO.FolderExists(strDir & "RadioRecordings") Then oFSO.CreateFolder strDir & "RadioRecordings"
If Not oFSO.FolderExists(strDir & "Runtime")         Then oFSO.CreateFolder strDir & "Runtime"

' ── 1. Prefer packaged exe ───────────────────────────────────────────────────
strExe = strDir & "RadioControlApp.exe"
If oFSO.FileExists(strExe) Then
    oShell.Run """" & strExe & """", 1, False
    WScript.Quit
End If

' ── 2. Python source fallback ────────────────────────────────────────────────
strScript = strDir & "app\radio_control_app.py"
If Not oFSO.FileExists(strScript) Then
    MsgBox "App files not found in:" & vbCrLf & strDir & vbCrLf & vbCrLf & _
           "Copy the full project folder to this machine and try again." & vbCrLf & _
           "Keep existing RadioRecordings, Runtime, and stations.txt.", _
           16, "Radio Control - Launch Failed"
    WScript.Quit
End If

' Try interpreters in order - window style 0 = completely hidden CMD
Dim interps(3)
interps(0) = "pythonw"
interps(1) = "py"
interps(2) = "python"
interps(3) = "python3"

Dim i, launched
launched = False
For i = 0 To 3
    On Error Resume Next
    oShell.Run interps(i) & " """ & strScript & """", 0, False
    If Err.Number = 0 Then
        launched = True
        Exit For
    End If
    Err.Clear
    On Error GoTo 0
Next

If Not launched Then
    MsgBox "Python interpreter not found." & vbCrLf & vbCrLf & _
           "Install Python 3 from:" & vbCrLf & _
           "https://www.python.org/downloads/" & vbCrLf & vbCrLf & _
           "During install, tick 'Add Python to PATH'.", _
           16, "Radio Control - Launch Failed"
End If
