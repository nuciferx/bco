$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "BCO Telegram Bot"

$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and
        $_.CommandLine -like "*bot.py*"
    }

foreach ($proc in $processes) {
    Stop-Process -Id $proc.ProcessId -Force
}

try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
} catch {
}

if ($processes) {
    Write-Output "Stopped $($processes.Count) BCO bot process(es)"
} else {
    Write-Output "BCO bot was not running"
}
