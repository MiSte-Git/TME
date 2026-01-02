#requires -version 5.1
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
Write-Host "Running syntax check (compileall)..."
& $Py -m compileall -q . | Out-Host

# --- Build EXE (onefile, windowed) ---
$Entry = Join-Path $RepoRoot "ui\app.py"
if (-not (Test-Path $Entry)) {
  throw "Entry point not found: $Entry"
}

# Optional icon if present
$Icon = Join-Path $RepoRoot "ui\assets\app.ico"
$IconArgs = @()
if (Test-Path $Icon) { $IconArgs = @("--icon", $Icon) }

Write-Host "Building EXE via PyInstaller..."
& $Py -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "TME" `
  @IconArgs `
  $Entry | Out-Host

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
