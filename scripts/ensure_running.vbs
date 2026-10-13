' Starts Retail OS (completely hidden) if it is not already answering on port 8000. Safe to run as often as you like.
' It runs under wscript.exe, which has no console window, so nothing flashes on screen when the keep-alive task runs.
Option Explicit
Dim sh, fso, http, up, logFile

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

up = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 2000, 2000, 3000, 3000
http.Open "GET", "http://127.0.0.1:8000/login", False
http.Send
If Err.Number = 0 Then up = True   ' any answer at all means the system is up
On Error GoTo 0

If Not up Then
  sh.CurrentDirectory = "C:\RetailOS"
  sh.Run """C:\RetailOS\.venv\Scripts\python.exe"" -m app.cli serve", 0, False   ' 0 = hidden window
  If Not fso.FolderExists("C:\RetailOS\data\logs") Then fso.CreateFolder "C:\RetailOS\data\logs"
  Set logFile = fso.OpenTextFile("C:\RetailOS\data\logs\ensure.log", 8, True)
  logFile.WriteLine Now & " Retail OS was not running: started it"
  logFile.Close
End If
