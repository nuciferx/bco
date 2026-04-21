$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path (Split-Path -Parent (Get-Command pythonw).Path) "pythonw.exe"
$botPath = Join-Path $repoRoot "bot.py"

Set-Location $repoRoot

$existing = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and
        $_.CommandLine -like "*bot.py*"
    } |
    Select-Object -First 1

if ($existing) {
    Write-Output "BCO bot already running (PID=$($existing.ProcessId))"
    exit 0
}

Start-Process -FilePath $pythonw -ArgumentList "`"$botPath`"" -WorkingDirectory $repoRoot -WindowStyle Hidden
Write-Output "BCO bot started"
