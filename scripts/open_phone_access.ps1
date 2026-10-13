# Lets phones and other computers on the SAME network (Wi-Fi or cable) open Retail OS on this PC.
# It adds one Windows Firewall rule: TCP port 8000, allowed only from the local network (not from the internet).
# Windows asks for permission once (the blue/yellow shield prompt): click Yes.
$ErrorActionPreference = "Stop"
$isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$name = "Retail OS (local network only)"
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null

$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))\.' } | Select-Object -ExpandProperty IPAddress
Write-Host ""
Write-Host "Done. On a phone connected to the same Wi-Fi / network, open:" -ForegroundColor Green
foreach ($ip in $ips) { Write-Host ("    http://{0}:8000" -f $ip) }
Write-Host ""
Write-Host "(This window closes in 20 seconds.)"
Start-Sleep -Seconds 20
