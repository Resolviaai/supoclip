# SupoClip Auto-Updater Windows Scheduled Task Installer
param(
    [int]$IntervalHours = 2
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VbsPath = Join-Path $PSScriptRoot "run_auto_updater_silent.vbs"
$TaskName = "SupoClip_AutoUpdater"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Installing SupoClip Auto-Updater Background Task" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Task Name: $TaskName"
Write-Host "Script:    $VbsPath"
Write-Host "Interval:  Every $IntervalHours hours (runs silently in background)"

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host ""
    Write-Host "[SUCCESS] Task '$TaskName' registered successfully!" -ForegroundColor Green
    Write-Host "SupoClip will automatically check for upstream updates every $IntervalHours hours."
} catch {
    Write-Host ""
    Write-Host "[ERROR] Failed to register task: $_" -ForegroundColor Red
    Write-Host "Please ensure you run this script in PowerShell or as Administrator."
}
