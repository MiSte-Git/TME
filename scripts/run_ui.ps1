#requires -version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Robuster als ".venv\Scripts\Activate.ps1": kein Eingriff in die aktuelle
# Shell (ExecutionPolicy-unabhaengig), da direkt die venv-python.exe aufgerufen wird.

$RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path
Set-Location $RepoRoot

$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  Write-Host "FEHLER: venv nicht gefunden ($VenvPy)." -ForegroundColor Red
  Write-Host "Bitte zuerst Setup ausfuehren (z.B. scripts\build_win.ps1, das die venv bei Bedarf anlegt)." -ForegroundColor Red
  exit 1
}

$AppEntry = Join-Path $RepoRoot "ui\app.py"
if (-not (Test-Path $AppEntry)) {
  Write-Host "FEHLER: Einstiegspunkt nicht gefunden: $AppEntry" -ForegroundColor Red
  exit 1
}

Write-Host "Verwende venv: $VenvPy"
& $VenvPy --version

& $VenvPy $AppEntry @args
exit $LASTEXITCODE
