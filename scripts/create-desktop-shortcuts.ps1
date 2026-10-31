# Creates Desktop shortcuts: "DarsAmooz - اجرا" and "DarsAmooz - توقف"
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $project "scripts\launch.bat"))) {
  $project = "D:\Projects\Dars-Amoz"
}
$desktop = [Environment]::GetFolderPath("Desktop")
$oneDriveDesktop = Join-Path $env:USERPROFILE "OneDrive\Desktop"
$desks = @($desktop)
if ((Test-Path $oneDriveDesktop) -and ($oneDriveDesktop -ne $desktop)) { $desks += $oneDriveDesktop }

$w = New-Object -ComObject WScript.Shell
$runFa = "DarsAmooz - " + [char]0x0627 + [char]0x062C + [char]0x0631 + [char]0x0627
$stopFa = "DarsAmooz - " + [char]0x062A + [char]0x0648 + [char]0x0642 + [char]0x0641

function New-FaShortcut([string]$desk, [string]$finalBase, [string]$bat, [int]$icon, [string]$desc) {
  $tmp = Join-Path $desk ("_tmp_" + $bat + ".lnk")
  $final = Join-Path $desk ($finalBase + ".lnk")
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
  if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Force }
  $s = $w.CreateShortcut($tmp)
  $s.TargetPath = Join-Path $project ("scripts\" + $bat)
  $s.WorkingDirectory = $project
  $s.WindowStyle = 1
  $s.Description = $desc
  $s.IconLocation = "$env:SystemRoot\System32\shell32.dll,$icon"
  $s.Save()
  [System.IO.File]::Move($tmp, $final)
  Write-Output ("created " + $final + " exists=" + [System.IO.File]::Exists($final))
}

foreach ($d in $desks) {
  New-FaShortcut $d $runFa "launch.bat" 137 "DarsAmooz launcher"
  New-FaShortcut $d $stopFa "kill.bat" 131 "DarsAmooz killer"
}