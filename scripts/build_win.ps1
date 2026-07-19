#requires -version 5.1
[CmdletBinding()]
param(
  # Ohne -Release: schneller lokaler Test-Build als --onedir (Ordner statt
  # gepackter Einzeldatei) mit wiederverwendetem PyInstaller-Analyse-Cache
  # (kein --clean). Mit -Release: --onefile (Distributions-EXE) plus --clean
  # fuer einen garantiert frischen, reproduzierbaren Build.
  [switch]$Release,
  # Erzwingt --clean auch fuer lokale --onedir-Builds (z.B. nach Aenderungen
  # an Abhaengigkeiten/Hidden-Imports, wenn der Analyse-Cache veraltet sein koennte).
  [switch]$Clean
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Test-DefenderRealtimeProtection {
  # Best-effort-Erkennung; liefert $null (statt Fehler), wenn sich der Status
  # nicht zuverlaessig ermitteln laesst (z.B. Drittanbieter-AV, fehlende Rechte).
  try {
    $status = Get-MpComputerStatus -ErrorAction Stop
    return [bool]$status.RealTimeProtectionEnabled
  } catch {
    return $null
  }
}

$DefenderActive = Test-DefenderRealtimeProtection
if ($DefenderActive -eq $true) {
  Write-Host "Hinweis: Windows Defender Echtzeitschutz scheint aktiv zu sein." -ForegroundColor Yellow
  Write-Host "PyInstaller-Builds koennen dadurch spuerbar langsamer sein (jede geschriebene Datei wird gescannt)." -ForegroundColor Yellow
  Write-Host "Tipp: 'C:\Projekte\TME' unter Windows-Sicherheit > Viren- & Bedrohungsschutz > Ausschluesse eintragen." -ForegroundColor Yellow
}

function Get-RepoRoot {
  $scriptDir = Split-Path -Parent $PSCommandPath
  return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Get-VenvPython([string]$RepoRoot) {
  $venvDir = Join-Path $RepoRoot ".venv"
  $venvPy  = Join-Path $venvDir "Scripts\python.exe"
  if (-not (Test-Path $venvPy)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
      throw "Python launcher 'py' not found. Install Python 3.11+ for Windows so 'py' exists."
    }
    Write-Host "Creating venv: $venvDir"
    py -3 -m venv $venvDir
  }
  return $venvPy
}

$RepoRoot = Get-RepoRoot
Set-Location $RepoRoot
Write-Host "RepoRoot: $RepoRoot"

# --- Local pip cache to avoid Errno 13 under AppData\Local\pip\cache ---
$PipCache = Join-Path $RepoRoot ".pip-cache"
Ensure-Dir $PipCache
$env:PIP_CACHE_DIR = $PipCache

# Optional: prevent user site-packages interference
$env:PYTHONNOUSERSITE = "1"

$Py = Get-VenvPython $RepoRoot
Write-Host "Using venv python: $Py"

# --- Upgrade pip tooling ---
& $Py -m pip install --upgrade pip setuptools wheel --no-cache-dir | Out-Host

# --- Install deps ---
# If you later add requirements.txt, this script will use it automatically.
$ReqTxt = Join-Path $RepoRoot "requirements.txt"
if (Test-Path $ReqTxt) {
  Write-Host "Installing deps from requirements.txt"
  & $Py -m pip install -r $ReqTxt --no-cache-dir | Out-Host
} else {
  Write-Warning "No requirements.txt found. Installing fallback deps."
  $Deps = @(
    "telethon",
    "odfpy",
    "pillow",
    "pytesseract",
    "easyocr",
    "PySide6"
  )
  & $Py -m pip install --no-cache-dir @Deps | Out-Host
}

# --- Install PyInstaller ---
& $Py -m pip install --no-cache-dir pyinstaller | Out-Host

# --- Optional: translations build (skip by default if bash is problematic) ---
# If you want it, set $env:TME_BUILD_TRANSLATIONS=1 before running.
$TransBuild = Join-Path $RepoRoot "ui\translations\build_qm.sh"
if ($env:TME_BUILD_TRANSLATIONS -eq "1" -and (Test-Path $TransBuild)) {
  if (Get-Command bash -ErrorAction SilentlyContinue) {
    try {
      Write-Host "Building translations..."
      & bash "$TransBuild" | Out-Host
    } catch {
      Write-Warning "Translations build failed, continuing: $($_.Exception.Message)"
    }
  } else {
    Write-Warning "Skipping translations build (bash not found)."
  }
}

# --- Syntax check ---
# Nur den Projekt-Code pruefen, NICHT .venv/build/dist mitkompilieren - sonst
# werden bei jedem Lauf unnoetig saemtliche Abhaengigkeiten (inkl. torch,
# PyInstaller selbst etc.) mitkompiliert, was den Syntax-Check unnoetig
# verlangsamt, ohne projektrelevante Fehler aufzudecken.
Write-Host "Running syntax check (compileall)..."
& $Py -m compileall -q . -x "[\\/](\.venv|build|dist)[\\/]" | Out-Host

# --- Build EXE (onedir standardmaessig / onefile mit -Release, windowed) ---
$Entry = Join-Path $RepoRoot "ui\app.py"
if (-not (Test-Path $Entry)) {
  throw "Entry point not found: $Entry"
}

# Optional icon if present
$Icon = Join-Path $RepoRoot "ui\assets\app.ico"
$IconArgs = @()
if (Test-Path $Icon) { $IconArgs = @("--icon", $Icon) }

$ModeArgs = if ($Release) { @("--onefile") } else { @("--onedir") }
# --clean wirft den PyInstaller-Analyse-Cache (build\TME\) weg und erzwingt eine
# komplette Neu-Analyse aller Imports - der groesste Zeitfaktor bei wiederholten
# lokalen Builds. Fuer -Release immer sauber bauen; sonst nur auf Wunsch (-Clean).
$UseClean = $Release -or $Clean
$CleanArgs = if ($UseClean) { @("--clean") } else { @() }

$ModeLabel = if ($Release) { "onefile (Release-Distribution)" } else { "onedir (schneller lokaler Test-Build)" }
Write-Host "Build-Modus: $ModeLabel"
Write-Host ("PyInstaller-Cache: " + $(if ($UseClean) { "wird verworfen (--clean)" } else { "wird wiederverwendet" }))

# keyring waehlt sein Backend zur Laufzeit dynamisch ueber importlib.metadata-
# Entry-Points statt normaler import-Statements - PyInstallers statische
# Analyse erkennt das nicht automatisch, daher explizite Hidden-Imports.
$KeyringHiddenImports = @(
  "--hidden-import", "keyring.backends.Windows",
  "--hidden-import", "keyring.backends.macOS",
  "--hidden-import", "keyring.backends.SecretService",
  "--hidden-import", "keyring.backends.kwallet",
  "--hidden-import", "keyring.backends.chainer",
  "--hidden-import", "keyring.backends.fail"
)

# Laufzeit-Ressourcen, die ui/app.py per Path(__file__).parent bzw. relativem
# Pfad laedt und die PyInstaller nicht automatisch erkennt (keine Python-
# Imports): Theme-QSS (inkl. des darin per relativem url() referenzierten
# checkbox-check.svg), Qt-Uebersetzungen und das Fenster-Icon. Analog zu
# TME_mac.spec, nur mit Windows-Pfadsyntax (";" statt ":" als Trenner
# zwischen Quelle/Ziel bei --add-data).
$DataArgs = @()
$ThemeDark = Join-Path $RepoRoot "ui\theme_dark.qss"
if (Test-Path $ThemeDark) { $DataArgs += @("--add-data", "$ThemeDark;ui") }
$ThemeLight = Join-Path $RepoRoot "ui\theme_light.qss"
if (Test-Path $ThemeLight) { $DataArgs += @("--add-data", "$ThemeLight;ui") }
$CheckboxSvg = Join-Path $RepoRoot "ui\checkbox-check.svg"
if (Test-Path $CheckboxSvg) { $DataArgs += @("--add-data", "$CheckboxSvg;ui") }
$WindowIcon = Join-Path $RepoRoot "Telegram-LibreOffice.png"
if (Test-Path $WindowIcon) { $DataArgs += @("--add-data", "$WindowIcon;.") }
$TranslationsDir = Join-Path $RepoRoot "ui\translations"
if (Test-Path $TranslationsDir) {
  Get-ChildItem -Path $TranslationsDir -Filter "app_*.qm" -File -ErrorAction SilentlyContinue | ForEach-Object {
    $DataArgs += @("--add-data", "$($_.FullName);ui\translations")
  }
}

Write-Host "Building EXE via PyInstaller..."
# Alle Flags zu EINEM Array zusammenfassen und nur einmal splatten (@PyInstallerArgs).
# Mehrere getrennte @Array-Splats in einer einzigen backtick-fortgesetzten
# Aufrufzeile fuehren in diesem Skript (mit [switch]-Parametern im param()-Block)
# reproduzierbar zu einem PowerShell-5.1-Marshalling-Fehler: PyInstaller erhaelt
# dabei den Skriptpfad ($Entry) nicht als eigenes Argument und bricht mit
# "unrecognized arguments: ...\ui\app.py" ab, noch bevor irgendein PyInstaller-
# Log erscheint. Ein einzelnes zusammengesetztes Array umgeht das zuverlaessig.
$PyInstallerArgs = @("--noconfirm") + $CleanArgs + $ModeArgs + @("--windowed", "--name", "TME") + $IconArgs + $KeyringHiddenImports + $DataArgs + @($Entry)
& $Py -m PyInstaller @PyInstallerArgs | Out-Host

# --- Result ---
$DistDir = Join-Path $RepoRoot "dist"
if (-not (Test-Path $DistDir)) {
  throw "dist\ not found. PyInstaller likely failed."
}

$Exe = Get-ChildItem -Path $DistDir -Recurse -Filter "TME.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Exe) {
  $Exe = Get-ChildItem -Path $DistDir -Recurse -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($Exe) {
  Write-Host ("Built EXE: " + $Exe.FullName)
} else {
  throw "Build finished but no .exe found under dist\."
}
