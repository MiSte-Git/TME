#requires -version 5.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Change to repo root
Set-Location (Join-Path $PSScriptRoot '..')

# Try to build translations (requires bash/lrelease; optional)
if (Test-Path 'ui\translations\build_qm.sh') {
  try {
    bash ui/translations/build_qm.sh | Write-Host
  } catch {
    Write-Warning 'Skipping translations build (bash/lrelease not found).'
  }
}

# Ensure PyInstaller
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
  py -m pip install pyinstaller | Out-Host
}

# Build via spec
py -m PyInstaller --noconfirm telegram_odt.spec

$exe = 'dist/Telegram-ODT/Telegram-ODT.exe'
if (Test-Path $exe) {
  Write-Host "Built: $exe"
} else {
  Write-Host 'Build finished, see dist/'
  Get-ChildItem dist | Format-List | Out-Host
}
