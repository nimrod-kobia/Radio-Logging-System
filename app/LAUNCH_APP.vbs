Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

' ── Resolve project root robustly ────────────────────────────────────────────
' VBS can live in app\ (normal) or root (fallback). Handle both.
strScriptFolder = oFSO.GetParentFolderName(WScript.ScriptFullName)
strParentFolder = oFSO.GetParentFolderName(strScriptFolder)

' Determine root: check all possible layouts
If oFSO.FileExists(strScriptFolder & "\radio_control_app.py") Then
    ' VBS is sitting directly inside app\
    strDir    = strParentFolder & "\"
    strScript = strScriptFolder & "\radio_control_app.py"
ElseIf oFSO.FileExists(strParentFolder & "\app\radio_control_app.py") Then
    ' Normal case: VBS in app\, root is one level up
    strDir    = strParentFolder & "\"
    strScript = strParentFolder & "\app\radio_control_app.py"
ElseIf oFSO.FileExists(strScriptFolder & "\app\radio_control_app.py") Then
    ' VBS is in root, app\ is a subfolder
    strDir    = strScriptFolder & "\"
    strScript = strScriptFolder & "\app\radio_control_app.py"
Else
    MsgBox "App files not found." & vbCrLf & vbCrLf & _
           "Searched in:" & vbCrLf & _
           "  " & strScriptFolder & "\radio_control_app.py" & vbCrLf & _
           "  " & strParentFolder & "\app\radio_control_app.py" & vbCrLf & _
           "  " & strScriptFolder & "\app\radio_control_app.py" & vbCrLf & vbCrLf & _
           "Copy the full project folder to this machine and try again." & vbCrLf & _
           "Keep existing RadioRecordings, Runtime, and stations.txt.", _
           16, "Radio Control - Launch Failed"
    WScript.Quit
End If

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
           "Fix: Install Python 3 from https://www.python.org/downloads/" & vbCrLf & _
           "During install, tick 'Add Python to PATH'." & vbCrLf & vbCrLf & _
           "If Python is already installed but not in PATH:" & vbCrLf & _
           "  Search Windows for 'Edit the system environment variables'" & vbCrLf & _
           "  -> Environment Variables -> Path -> New" & vbCrLf & _
           "  Add the folder containing python.exe (e.g. C:\Python313\)", _
           16, "Radio Control - Launch Failed"
End If
