#Requires -Version 5.1
<#
.SYNOPSIS
  Interactive launcher for DarsAmooz generate (Persian prompts, UTF-8).
.PARAMETER Chapter
  Chapter number (skips prompt when set).
.PARAMETER Count
  Question count (skips prompt when set).
.PARAMETER Sample
  Use bundled sample output (no API).
#>
param(
    [int]$Chapter = -1,
    [int]$Count = -1,
    [switch]$Sample
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
$LogFile = Join-Path $ProjectRoot 'data\exports\launcher.log'

function Write-PidFile {
    $payload = @(
        "launcher_pid=$PID"
        "started=$(Get-Date -Format o)"
        "project=$ProjectRoot"
    ) -join "`n"
    [System.IO.File]::WriteAllText($PidFile, $payload + "`n", [System.Text.UTF8Encoding]::new($false))
}

function Read-PositiveInt([string]$prompt, [int]$default) {
    $raw = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($raw)) { return $default }
    $n = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$n) -or $n -lt 1) {
        Write-Host "مقدار نامعتبر بود؛ از پیش‌فرض $default استفاده می‌شود." -ForegroundColor Yellow
        return $default
    }
    return $n
}

Write-Host ""
Write-Host "========== درس‌آموز / DarsAmooz ==========" -ForegroundColor Cyan
Write-Host "پروژه: $ProjectRoot"
Write-PidFile
Write-Host "PID لانچر: $PID (ثبت در .darsamooz.pid)" -ForegroundColor DarkGray

$venvActivate = Join-Path $ProjectRoot '.venv\Scripts\Activate.ps1'
$darsamoozExe = Join-Path $ProjectRoot '.venv\Scripts\darsamooz.exe'
if (-not (Test-Path $venvActivate)) {
    throw "محیط مجازی پیدا نشد: $venvActivate"
}
if (-not (Test-Path $darsamoozExe)) {
    throw "darsamooz.exe پیدا نشد. اول pip install -e . را اجرا کنید."
}

. $venvActivate

if ($Chapter -lt 1) {
    $Chapter = Read-PositiveInt 'شماره فصل را وارد کنید' 1
}
if ($Count -lt 1) {
    $Count = Read-PositiveInt 'تعداد سؤال را وارد کنید' 10
}

Write-Host ""
Write-Host "اجرا: فصل=$Chapter | تعداد=$Count$(if ($Sample) { ' | حالت نمونه' })" -ForegroundColor Green

$exportsDir = Join-Path $ProjectRoot 'data\exports'
New-Item -ItemType Directory -Force -Path $exportsDir | Out-Null
$prefix = "phys10-ch$Chapter"
$mdPath = Join-Path $exportsDir "$prefix.md"

$genArgs = @('generate', '--chapter', "$Chapter", '--count', "$Count", '--out-prefix', $prefix)
if ($Sample) { $genArgs += '--sample' }

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Run generate as a child so we can record its PID for the killer.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $darsamoozExe
$psi.Arguments = ($genArgs -join ' ')
$psi.WorkingDirectory = $ProjectRoot
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $false
$psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
$psi.StandardErrorEncoding  = [System.Text.UTF8Encoding]::new($false)

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
[void]$proc.Start()

$childPayload = @(
    "launcher_pid=$PID"
    "generate_pid=$($proc.Id)"
    "started=$(Get-Date -Format o)"
    "project=$ProjectRoot"
) -join "`n"
[System.IO.File]::WriteAllText($PidFile, $childPayload + "`n", [System.Text.UTF8Encoding]::new($false))
Write-Host "PID تولید: $($proc.Id)" -ForegroundColor DarkGray

$stdout = $proc.StandardOutput.ReadToEnd()
$stderr = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()
if ($stdout) { Write-Host $stdout }
if ($stderr) { Write-Host $stderr -ForegroundColor Yellow }

$exitCode = $proc.ExitCode
if ($exitCode -ne 0) {
    Write-Host "خطا: darsamooz با کد $exitCode خارج شد." -ForegroundColor Red
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    exit $exitCode
}

if (-not (Test-Path $mdPath)) {
    Write-Host "هشدار: فایل Markdown پیدا نشد: $mdPath" -ForegroundColor Yellow
} else {
    Write-Host "باز کردن فایل Markdown..." -ForegroundColor Cyan
    Invoke-Item $mdPath
}
Write-Host "باز کردن پوشه خروجی..." -ForegroundColor Cyan
Invoke-Item $exportsDir

if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
Write-Host "تمام شد." -ForegroundColor Green
