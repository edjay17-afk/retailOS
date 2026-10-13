' Starts the Retail OS public tunnel (completely hidden) if ngrok is not already running. Safe to run as often as you like.
' It runs under wscript.exe, which has no console window, so nothing flashes on screen when the keep-alive task runs.
' The tunnel is what makes https://courier-cacti-undecided.ngrok-free.dev reach this computer's Retail OS on port 8000.
Option Explicit
Dim sh, wmi, procs, fso, logFile

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='ngrok.exe'")

If procs.Count = 0 Then
  sh.CurrentDirectory = "C:\RetailOS"
  sh.Run "cmd /c ngrok start retailos --log=stdout >> C:\RetailOS\data\logs\ngrok.log 2>&1", 0, False   ' 0 = hidden window
  If Not fso.FolderExists("C:\RetailOS\data\logs") Then fso.CreateFolder "C:\RetailOS\data\logs"
  Set logFile = fso.OpenTextFile("C:\RetailOS\data\logs\ensure.log", 8, True)
  logFile.WriteLine Now & " ngrok tunnel was not running: started it"
  logFile.Close
End If
