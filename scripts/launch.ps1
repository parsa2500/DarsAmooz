#Requires -Version 5.1
<#
.SYNOPSIS
  Start DarsAmooz local web UI and open the browser.
.PARAMETER Chapter
  Prefill chapter in the UI via query string.
.PARAMETER Count
  Prefill question count via query string.
.PARAMETER Sample
  Prefill sample mode (sample=1).
.PARAMETER Port
  Local port (default 8765).
.PARAMETER NoBrowser
  Do not open the default browser.
#>
param(
    [int]$Chapter = -1,
    [int]$Count = -1,
    [switch]$Sample,
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}
chcp 65001 | Out-Null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PidFile = Join-Path $ProjectRoot '.darsamooz.pid'
$HostName = '127.0.0.1'
$BaseUrl = "http://${HostName}:$Port"

function Write-PidFile([int]$WebPid) {
    $payload = @(
        "launcher_pid=$PID"
        "web_pid=$WebPid"
        "started=$(Get-Date -Format o)"
        "project=$ProjectRoot"
        "url=$BaseUrl"
    ) -join "`n"
    [System.IO.File]::WriteAllText($PidFile, $payload + "`n", [System.Text.UTF8Encoding]::new($false))
}

Write-Host ""
Write-Host "========== درس‌آموز / DarsAmooz ==========" -ForegroundColor Cyan
Write-Host "پروژه: $ProjectRoot"
Write-Host "رابط وب محلی: $BaseUrl" -ForegroundColor Green

$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "محیط مجازی پیدا نشد: $venvPython"
}

# Quick import check for web extras
$check = & $venvPython -c "import fastapi, uvicorn; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "بسته‌های وب نصب نیست. در حال نصب fastapi و uvicorn…" -ForegroundColor Yellow
    & $venvPython -m pip install -e ".[web]"
    if ($LASTEXITCODE -ne 0) {
        throw "نصب وابستگی وب ناموفق بود. دستی اجرا کنید: pip install -e `".[web]`""
    }
}

$queryParts = @()
if ($Chapter -ge 1) { $queryParts += "chapter=$Chapter" }
if ($Count -ge 1) { $queryParts += "count=$Count" }
if ($Sample) { $queryParts += "sample=1" }
$openUrl = $BaseUrl
if ($queryParts.Count -gt 0) {
    $openUrl = "$BaseUrl/?" + ($queryParts -join '&')
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $venvPython
$psi.Arguments = "-m darsamooz.web --host $HostName --port $Port"
$psi.WorkingDirectory = $ProjectRoot
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError = $false

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
[void]$proc.Start()
Write-PidFile -WebPid $proc.Id
Write-Host "PID سرور: $($proc.Id) (ثبت در .darsamooz.pid)" -ForegroundColor DarkGray

# Wait until HTTP responds, then open browser
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    if ($proc.HasExited) {
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
        throw "سرور وب زود خارج شد (کد $($proc.ExitCode))."
    }
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl/api/meta" -UseBasicParsing -TimeoutSec 1
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 250
    }
}

if (-not $ready) {
    Write-Host "هشدار: هنوز پاسخ meta نیامد؛ مرورگر را باز می‌کنیم." -ForegroundColor Yellow
}

if (-not $NoBrowser) {
    Write-Host "باز کردن مرورگر: $openUrl" -ForegroundColor Cyan
    Start-Process $openUrl
}

Write-Host "برای توقف: میانبر «DarsAmooz - توقف» یا Ctrl+C در این پنجره." -ForegroundColor DarkGray
try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    Write-Host "سرور متوقف شد." -ForegroundColor Green
}
