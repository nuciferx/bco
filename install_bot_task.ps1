$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "BCO Telegram Bot"
$runScript = Join-Path $repoRoot "run_bot.ps1"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$startupDir = [Environment]::GetFolderPath("Startup")
$startupCmdPath = Join-Path $startupDir "BCO Telegram Bot.cmd"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""

$triggerStartup = New-ScheduledTaskTrigger -AtStartup
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($triggerStartup, $triggerLogon) `
    -Settings $settings `
    -Principal $principal

try {
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output "Installed scheduled task: $taskName"
} catch {
    $cmdContent = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`"`r`n"
    Set-Content -Path $startupCmdPath -Value $cmdContent -Encoding ASCII
    Write-Warning "Scheduled Task install failed; installed Startup fallback instead at $startupCmdPath"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runScript
}
