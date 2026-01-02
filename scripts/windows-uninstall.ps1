#requires -version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

param(
  [string]$AppName = "TME",
  [switch]$AllUsers
)

function Remove-IfExists([string]$Path) {
  if (Test-Path $Path) {
    Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
    Write-Host ("Removed: {0}" -f $Path)
  } else {
    Write-Host ("Not found: {0}" -f $Path)
  }
}

function Remove-FileIfExists([string]$Path) {
  if (Test-Path $Path) {
    Remove-Item -Path $Path -Force -ErrorAction Stop
    Write-Host ("Removed: {0}" -f $Path)
  } else {
    Write-Host ("Not found: {0}" -f $Path)
  }
}

# Install dir
if ($AllUsers) {
  $InstallDir = Join-Path $env:ProgramFiles $AppName
} else {
  $InstallDir = Join-Path $env:LocalAppData $AppName
}

Write-Host ("Uninstalling {0} from: {1}" -f $AppName, $InstallDir)
Remove-IfExists $InstallDir

# Start Menu shortcuts
if ($AllUsers) {
  $ProgramsRoot = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
} else {
  $ProgramsRoot = Join-Path $env:AppData "Microsoft\Windows\Start Menu\Programs"
}
$StartMenuDir = Join-Path $ProgramsRoot $AppName
Remove-IfExists $StartMenuDir

# Desktop shortcut (remove if present)
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$DesktopLink = Join-Path $DesktopDir ("{0}.lnk" -f $AppName)
Remove-FileIfExists $DesktopLink

Write-Host "Done."
