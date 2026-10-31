#Requires -Version 5.1
<#
.SYNOPSIS
  Stop only DarsAmooz-related processes (never unrelated Python).
#>
$ErrorActionPreference = "Continue"
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}
chcp 65001 | Out-Null

$ProjectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$PidFile = Join-Path $ProjectRoot ".darsamooz.pid"
$stopped = New-Object System.Collections.Generic.List[string]

function Stop-TrackedPid([int]$ProcessId, [string]$reason) {
    if ($ProcessId -le 0) { return }
    if ($ProcessId -eq $PID) { return }
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
    } catch {
        Write-Host ("PID {0} دیگر در حال اجرا نیست ({1})." -f $ProcessId, $reason) -ForegroundColor DarkGray
        return
    }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        $msg = "متوقف شد: PID=$ProcessId Name=$($p.ProcessName) — $reason"
        Write-Host $msg -ForegroundColor Yellow
        [void]$script:stopped.Add($msg)
    } catch {
        Write-Host ("نتوانست PID {0} را متوقف کند: {1}" -f $ProcessId, $_) -ForegroundColor Red
    }
}

function Test-IsDarsAmoozCommand([string]$cmd) {
    if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }
    # "darsamooz" does not appear in the folder name "Dars-Amoz", so a simple
    # substring match will not kill unrelated venv python processes.
    if ($cmd -match '(?i)darsamooz') { return $true }
    if ($cmd -match '(?i)scripts\\launch\.ps1') { return $true }
    if ($cmd -match '(?i)scripts\\launch\.bat') { return $true }
    return $false
}

Write-Host ""
Write-Host "========== توقف درس‌آموز / DarsAmooz ==========" -ForegroundColor Cyan
Write-Host ("پروژه: {0}" -f $ProjectRoot)

if (Test-Path -LiteralPath $PidFile) {
    $text = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8
    Write-Host ("خواندن فایل PID: {0}" -f $PidFile)
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match '^(launcher_pid|generate_pid|web_pid)=(\d+)$') {
            Stop-TrackedPid -ProcessId ([int]$Matches[2]) -reason $Matches[1]
        }
    }
} else {
    Write-Host "فایل PID موجود نیست (.darsamooz.pid)." -ForegroundColor DarkGray
}

$candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^(python|pythonw|py|pwsh|powershell)\.exe$' -and
    (Test-IsDarsAmoozCommand $_.CommandLine)
}

foreach ($c in $candidates) {
    if ([int]$c.ProcessId -eq $PID) { continue }
    Stop-TrackedPid -ProcessId ([int]$c.ProcessId) -reason ("cmdline: " + $c.Name)
}

if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "فایل PID پاک شد." -ForegroundColor DarkGray
}

if ($stopped.Count -eq 0) {
    Write-Host "هیچ فرایند مربوط به درس‌آموز برای توقف پیدا نشد." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host ("جمع متوقف‌شده‌ها: {0}" -f $stopped.Count) -ForegroundColor Green
    $stopped | ForEach-Object { Write-Host (" - {0}" -f $_) }
}
