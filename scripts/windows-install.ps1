#requires -version 5.1

param(
  [string]$AppName = "TME",
  [string]$SourceExe = "",
  [switch]$AllUsers,
  [switch]$CreateDesktopShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-RepoRoot {
  $scriptDir = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function New-Shortcut {
  param(
    [Parameter(Mandatory=$true)][string]$LinkPath,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][string]$WorkingDirectory,
    [string]$IconLocation = ""
  )

  Ensure-Dir (Split-Path -Parent $LinkPath)

  $wsh = New-Object -ComObject WScript.Shell
  $sc = $wsh.CreateShortcut($LinkPath)
  $sc.TargetPath = $TargetPath
  $sc.WorkingDirectory = $WorkingDirectory
  if ($IconLocation -and (Test-Path $IconLocation)) {
    $sc.IconLocation = $IconLocation
  }
  $sc.Save()
}

$RepoRoot = Get-RepoRoot

if ([string]::IsNullOrWhiteSpace($SourceExe)) {
  $SourceExe = Join-Path $RepoRoot ("dist\{0}.exe" -f $AppName)
} elseif (-not [System.IO.Path]::IsPathRooted($SourceExe)) {
  $SourceExe = Join-Path $RepoRoot $SourceExe
}

$rp = Resolve-Path $SourceExe -ErrorAction SilentlyContinue
if ($rp) {
  $SourceExe = $rp.Path
} else {
  $SourceExe = $null
}

if (-not $SourceExe -or -not (Test-Path $SourceExe)) {
  throw ("Source EXE not found. Build first, expected: dist\{0}.exe (or pass -SourceExe). RepoRoot={1}" -f $AppName, $RepoRoot)
}

# Install dir
if ($AllUsers) {
  $InstallDir = Join-Path $env:ProgramFiles $AppName
} else {
  $InstallDir = Join-Path $env:LocalAppData $AppName
}
Ensure-Dir $InstallDir

$DestExe = Join-Path $InstallDir ("{0}.exe" -f $AppName)

Write-Host ("Installing {0} to: {1}" -f $AppName, $InstallDir)
Copy-Item -Path $SourceExe -Destination $DestExe -Force

# Start Menu shortcut
if ($AllUsers) {
  $ProgramsRoot = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
} else {
  $ProgramsRoot = Join-Path $env:AppData "Microsoft\Windows\Start Menu\Programs"
}
$StartMenuDir = Join-Path $ProgramsRoot $AppName
$StartMenuLink = Join-Path $StartMenuDir ("{0}.lnk" -f $AppName)

Write-Host ("Creating Start Menu shortcut: {0}" -f $StartMenuLink)
New-Shortcut -LinkPath $StartMenuLink -TargetPath $DestExe -WorkingDirectory $InstallDir -IconLocation $DestExe

# Optional Desktop shortcut
if ($CreateDesktopShortcut) {
  $DesktopDir = [Environment]::GetFolderPath("Desktop")
  $DesktopLink = Join-Path $DesktopDir ("{0}.lnk" -f $AppName)
  Write-Host ("Creating Desktop shortcut: {0}" -f $DesktopLink)
  New-Shortcut -LinkPath $DesktopLink -TargetPath $DestExe -WorkingDirectory $InstallDir -IconLocation $DestExe
}

Write-Host "Done."
Write-Host ("Run: {0}" -f $DestExe)
