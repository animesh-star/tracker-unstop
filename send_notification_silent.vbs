' Silent Toast Launcher for Windows
' Guarantees zero terminal windows, zero command prompts, and zero console flashes.
Option Explicit
Dim oShell, sCmd, i, args
Set oShell = CreateObject("WScript.Shell")
Set args = WScript.Arguments

If args.Count = 0 Then
    WScript.Quit 1
End If

sCmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass"
For i = 0 To args.Count - 1
    sCmd = sCmd & " """ & Replace(args(i), """", """""") & """"
Next

oShell.Run sCmd, 0, False
