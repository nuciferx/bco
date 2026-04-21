$ErrorActionPreference = "Stop"

$taskName = "BCO Telegram Bot"
$startupDir = [Environment]::GetFolderPath("Startup")
$startupCmdPath = Join-Path $startupDir "BCO Telegram Bot.cmd"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Output "TaskName: $taskName"
    Write-Output "TaskState: $($task.State)"
    Write-Output "LastRunTime: $($taskInfo.LastRunTime)"
    Write-Output "LastTaskResult: $($taskInfo.LastTaskResult)"
    Write-Output "NextRunTime: $($taskInfo.NextRunTime)"
} else {
    Write-Output "TaskName: NOT_INSTALLED"
}

if (Test-Path $startupCmdPath) {
    Write-Output "StartupFallback: INSTALLED"
    Write-Output "StartupPath: $startupCmdPath"
} else {
    Write-Output "StartupFallback: NOT_INSTALLED"
}

$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and
        $_.CommandLine -like "*bot.py*"
    }

if ($processes) {
    foreach ($proc in $processes) {
        Write-Output "ProcessId: $($proc.ProcessId)"
        Write-Output "CommandLine: $($proc.CommandLine)"
    }
} else {
    Write-Output "ProcessId: NOT_RUNNING"
}
