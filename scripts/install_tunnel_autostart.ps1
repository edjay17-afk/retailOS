# Registers the Windows task that keeps the Retail OS public tunnel (ngrok) running without anyone starting it:
#   - when you sign in to Windows,
#   - when the PC wakes up from sleep,
#   - every 5 minutes (restarts it if it ever stops).
# It runs with no window at all. It does not wake the PC; when the PC is asleep or off, the public address
# (https://courier-cacti-undecided.ngrok-free.dev) is simply unreachable until the PC is on again.
$ErrorActionPreference = "Stop"
$script  = "C:\RetailOS\scripts\ensure_tunnel.vbs"
# wscript.exe has no console window, so nothing flashes on screen every time the task runs
$action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B //Nologo `"$script`"" -WorkingDirectory "C:\RetailOS"

$logon   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)

$eventClass = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$resume = New-CimInstance -CimClass $eventClass -ClientOnly
$resume.Enabled = $true
$resume.Subscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Power-Troubleshooter''] and EventID=1]]</Select></Query></QueryList>'

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "RetailOS-Tunnel" -Action $action -Trigger @($logon, $repeat, $resume) -Settings $settings -Description "Keeps the Retail OS public tunnel (ngrok) running (sign-in, wake from sleep, every 5 minutes)" -Force | Out-Null
Write-Host "Registered. The public tunnel will start by itself at sign-in, after sleep, and is restarted within 5 minutes if it stops."
