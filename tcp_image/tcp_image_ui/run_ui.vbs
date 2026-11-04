' ---------------------------------------------------------------------------
' run_ui.vbs -- start tcp_image_ui with NO console window.
'
' Double-click this file (or the desktop shortcut it creates) instead of
' run.bat: the black cmd box never appears.  It does exactly what run.bat does
' (same Python, same TCL_LIBRARY/TK_LIBRARY pinning), just with the window
' hidden:
'
'     cmd /c "run.bat <your args> --hidden-run"      window style 0 = hidden
'
' Exceptions, where a console IS wanted: if you pass --selftest or --console
' the window is shown, because those modes exist to print text.
'
' If app.py fails to start, run.bat returns a non-zero exit code and we pop up
' a message box with the tail of run_log.txt -- that is where pythonw.exe's
' stdout/stderr went, since it has no console to write to.
'
' ASCII-only on purpose: Windows Script Host reads .vbs files with the ANSI
' codepage, so Chinese literals in here would come out as mojibake.  The one
' Chinese string we need (the desktop shortcut name) is built with ChrW().
' ---------------------------------------------------------------------------
Option Explicit

Const BAT_NAME = "run.bat"
Const LOG_NAME = "run_log.txt"
Const LNK_BASE = "tcp_image_ui "

Dim sh, fso, here, i, extra, wantConsole, style, cmdline, rc, logPath

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here    = fso.GetParentFolderName(WScript.ScriptFullName)
logPath = fso.BuildPath(here, LOG_NAME)

If Not fso.FileExists(fso.BuildPath(here, BAT_NAME)) Then
    MsgBox BAT_NAME & " not found next to this script:" & vbCrLf & here, _
           0 + 16, "tcp_image_ui"
    WScript.Quit 1
End If

' ---- forward our command line to run.bat ---------------------------------
extra = ""
wantConsole = False
For i = 0 To WScript.Arguments.Count - 1
    extra = extra & " " & Quote(WScript.Arguments(i))
    If LCase(WScript.Arguments(i)) = "--selftest" Or _
       LCase(WScript.Arguments(i)) = "--console" Then
        wantConsole = True
    End If
Next

If wantConsole Then
    style = 1        ' normal window: the run wants to print something
Else
    style = 0        ' hidden: this is the whole point of the script
End If

cmdline = Quote(fso.BuildPath(here, BAT_NAME)) & extra & " --hidden-run"

EnsureDesktopShortcut

' cmd /c swallows the outer pair of quotes, so the whole command line has to
' be wrapped in a second pair.
rc = sh.Run(Quote(sh.ExpandEnvironmentStrings("%ComSpec%")) & " /c " & _
            Quote(cmdline), style, True)

If rc <> 0 Then
    MsgBox "tcp_image_ui failed to start (exit code " & rc & ")." & vbCrLf & _
           vbCrLf & logPath & vbCrLf & vbCrLf & LogTail(logPath, 1500), _
           0 + 48, "tcp_image_ui"
End If

WScript.Quit rc


' ---------------------------------------------------------------- helpers --

Function Quote(s)
    Quote = Chr(34) & s & Chr(34)
End Function

' Create or refresh a desktop shortcut that runs this script, so the user has
' a one-click, zero-flash entry point.  Idempotent (also self-heals if the
' project folder is moved).  Silent: on any failure we just do not have one.
' The name is  "tcp_image_ui " + U+4E0A U+4F4D U+673A  ("host computer"),
' spelled with ChrW so that this file stays pure ASCII.
Sub EnsureDesktopShortcut()
    Dim desk, lnk, ws, ico, py
    On Error Resume Next
    desk = sh.SpecialFolders("Desktop")
    If desk = "" Then Exit Sub
    lnk = fso.BuildPath(desk, LNK_BASE & ChrW(&H4E0A) & ChrW(&H4F4D) & _
                        ChrW(&H673A) & ".lnk")
    Set ws = sh.CreateShortcut(lnk)
    ws.TargetPath = sh.ExpandEnvironmentStrings("%SystemRoot%") & _
                    "\System32\wscript.exe"
    ws.Arguments = Quote(WScript.ScriptFullName)
    ws.WorkingDirectory = here
    ws.WindowStyle = 1
    ws.Description = LNK_BASE & "GUI (no console window)"
    py = "E:\fpga\vivado\2025.2.1\Vivado\tps\win64\python-3.13.0\python.exe"
    If fso.FileExists(py) Then
        ico = py & ",0"
        ws.IconLocation = ico
    End If
    ws.Save
End Sub

' Last n characters of a text file, or "" if it is missing or unreadable.
Function LogTail(path, n)
    Dim f, ts, s
    LogTail = ""
    If Not fso.FileExists(path) Then Exit Function
    On Error Resume Next
    Set f = fso.GetFile(path)
    If f.Size = 0 Then Exit Function
    If f.Size > 262144 Then
        LogTail = "(run_log.txt is " & f.Size & " bytes; tail not shown)"
        Exit Function
    End If
    Set ts = fso.OpenTextFile(path, 1)
    s = ts.ReadAll
    ts.Close
    If Err.Number <> 0 Then
        LogTail = "(could not read run_log.txt)"
        Exit Function
    End If
    If Len(s) > n Then s = "..." & Right(s, n)
    LogTail = s
End Function
