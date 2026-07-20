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

# build_win.ps1 baut standardmaessig --onedir (dist\<App>\<App>.exe + ein
# _internal\-Ordner mit den Theme-/Uebersetzungs-Ressourcen aus dem letzten
# Build-Fix) und nur mit -Release --onefile (dist\<App>.exe als gepackte
# Einzeldatei). Ohne -SourceExe werden daher beide moeglichen Ergebnisse
# geprueft, onedir zuerst (der lokale Standard-Build-Modus); existieren
# beide (z.B. nach abwechselndem Testen/Release-Bauen), gewinnt das neuere
# (LastWriteTime).
if ([string]::IsNullOrWhiteSpace($SourceExe)) {
  $OneDirExe = Join-Path $RepoRoot ("dist\{0}\{0}.exe" -f $AppName)
  $OneFileExe = Join-Path $RepoRoot ("dist\{0}.exe" -f $AppName)
  $OneDirItem = Get-Item -Path $OneDirExe -ErrorAction SilentlyContinue
  $OneFileItem = Get-Item -Path $OneFileExe -ErrorAction SilentlyContinue

  if ($OneDirItem -and $OneFileItem) {
    if ($OneFileItem.LastWriteTime -gt $OneDirItem.LastWriteTime) {
      $SourceExe = $OneFileItem.FullName
      Write-Host ("Info: onedir- und onefile-Build gefunden - verwende das neuere onefile-Ergebnis ({0}, {1})." -f $SourceExe, $OneFileItem.LastWriteTime)
    } else {
      $SourceExe = $OneDirItem.FullName
      Write-Host ("Info: onedir- und onefile-Build gefunden - verwende das neuere onedir-Ergebnis ({0}, {1})." -f $SourceExe, $OneDirItem.LastWriteTime)
    }
  } elseif ($OneDirItem) {
    $SourceExe = $OneDirItem.FullName
  } elseif ($OneFileItem) {
    $SourceExe = $OneFileItem.FullName
  } else {
    $SourceExe = $OneDirExe
  }
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
  throw ("Source EXE not found. Build first, expected one of: dist\{0}\{0}.exe (onedir) or dist\{0}.exe (onefile) (or pass -SourceExe). RepoRoot={1}" -f $AppName, $RepoRoot)
}

# onedir-Erkennung ueber den PyInstaller-typischen _internal\-Ordner neben
# der EXE - robust unabhaengig davon, ob der Pfad automatisch erkannt oder
# per -SourceExe uebergeben wurde (Vorgabe 2: -SourceExe wird nicht neu
# gesucht, nur das Kopierverhalten haengt von der tatsaechlichen
# Ordnerstruktur ab).
$SourceDir = Split-Path -Parent $SourceExe
$IsOneDir = Test-Path (Join-Path $SourceDir "_internal")

# Install dir
if ($AllUsers) {
  $InstallDir = Join-Path $env:ProgramFiles $AppName
} else {
  $InstallDir = Join-Path $env:LocalAppData $AppName
}
Ensure-Dir $InstallDir

$DestExe = Join-Path $InstallDir ("{0}.exe" -f $AppName)

if ($IsOneDir) {
  # Kompletten Ordner uebernehmen (inkl. _internal\ mit Theme-/Uebersetzungs-
  # dateien) - nur die EXE zu kopieren wuerde die installierte Version ohne
  # Icons/Themes/Uebersetzungen starten lassen.
  Write-Host ("Installing {0} (onedir) to: {1}" -f $AppName, $InstallDir)
  Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force
} else {
  Write-Host ("Installing {0} (onefile) to: {1}" -f $AppName, $InstallDir)
  Copy-Item -Path $SourceExe -Destination $DestExe -Force
}

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
