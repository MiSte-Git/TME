"""Legt für TME einen Eintrag im Anwendungsmenü/Startmenü an, je Plattform.

Baut bewusst KEIN .deb/.rpm/.msi/.dmg - Vorgehen 1:1 vom bereits produktiven
Vorbild in PDF-Translator übernommen (siehe dessen
bootstrap/desktop_integration.py-Docstring für die volle Begründung: keines
dieser Formate kann ein Anheften an Taskleiste/Dock programmatisch erzwingen,
das verbieten Windows/macOS aus Sicherheitsgründen ohnehin, also leistet ein
leichtgewichtiger, rechtefreier Menüeintrag genau dasselbe).

- Linux: eine .desktop-Datei (freedesktop.org Desktop Entry Spec) unter
  ~/.local/share/applications/ - ohne root und ohne Systempaket.
- Windows: eine .lnk-Verknüpfung im Pro-Benutzer-Startmenü, erzeugt über
  PowerShells WScript.Shell-COM-Objekt (keine pywin32-Abhängigkeit nur für
  diese eine Verknüpfung nötig).
- macOS: ein minimal von Hand gebautes .app-Bundle (Contents/MacOS +
  Contents/Info.plist) unter ~/Applications.

Entwickler-Variante, analog zum Vorbild: derselbe Mechanismus, zeigt aber auf
ein Quellcode-Checkout und den Interpreter, mit dem normalerweise gestartet
wird (venv/pyenv/System-Python), statt auf die versteckte
Bootstrapper-Installation - eigener Eintragsname, beide können nebeneinander
bestehen:

    python -m bootstrap.desktop_integration --dev

Icon: assets/icon.{png,ico,icns} im App-Code, falls vorhanden (noch nicht
angelegt, siehe TME-Backlog.md); fällt mangels dessen unter Linux auf die
bereits vorhandene Telegram-LibreOffice.png im Repo-Root zurück (das
freedesktop.org-Format akzeptiert beliebige Bildformate für Icon=), unter
Windows/macOS auf kein Icon (Standard-Icon des Interpreters bzw. keines im
Bundle) - vollwertige .ico-/.icns-Varianten aus Telegram-LibreOffice.png zu
erzeugen ist ein offener Folgeschritt (siehe TME-Backlog.md), analog zu
PDF-Translators tools/build_icon.py.
"""
from __future__ import annotations

import argparse
import os
import platform
import stat
import subprocess
import sys
from pathlib import Path

APP_DISPLAY_NAME = "TME"
APP_ENTRY_SLUG = "tme"
DEV_DISPLAY_NAME = "TME (dev)"
DEV_ENTRY_SLUG = "tme-dev"
# Muss QApplication.setApplicationName(APP_NAME) in ui/app.py entsprechen
# (dort APP_NAME = "TME") - Qt leitet daraus den X11-WM_CLASS-Klassennamen
# bzw. die Wayland-app_id ab.
APP_WM_CLASS = "TME"
_WINDOWS_SHORTCUT_TIMEOUT_SECONDS = 30

_ICON_FILE_BY_PLATFORM = {
    "Windows": "icon.ico",
    "Darwin": "icon.icns",
    "Linux": "icon.png",
}
# Fallback, solange assets/icon.* noch nicht existiert - nur unter Linux
# genutzt (siehe Modul-Docstring).
_LEGACY_LINUX_ICON = "Telegram-LibreOffice.png"


class DesktopIntegrationError(RuntimeError):
    """Wird geworfen, wenn der Menüeintrag nicht angelegt werden konnte."""


def linux_applications_dir() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "applications"


def windows_start_menu_programs_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def macos_applications_dir() -> Path:
    return Path.home() / "Applications"


def default_icon_path(app_source_dir: Path, system: str | None = None) -> Path | None:
    """assets/icon.<ext> im App-Code für diese Plattform, sonst (nur Linux)
    der Fallback auf die bereits vorhandene Telegram-LibreOffice.png, sonst
    None."""
    system = system if system is not None else platform.system()
    filename = _ICON_FILE_BY_PLATFORM.get(system)
    if filename is not None:
        candidate = app_source_dir / "assets" / filename
        if candidate.is_file():
            return candidate
    if system == "Linux":
        legacy = app_source_dir / _LEGACY_LINUX_ICON
        if legacy.is_file():
            return legacy
    return None


def _resolve_icon(app_source_dir: Path, icon_path: Path | None, system: str) -> Path | None:
    return icon_path if icon_path is not None else default_icon_path(app_source_dir, system)


# --- Linux -------------------------------------------------------------


def _linux_desktop_entry_content(
    app_source_dir: Path,
    venv_python: Path,
    icon_path: Path | None,
    display_name: str = APP_DISPLAY_NAME,
) -> str:
    icon_line = f"Icon={icon_path}" if icon_path else f"Icon={APP_ENTRY_SLUG}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={display_name}\n"
        "Comment=Telegram-Nachrichten nach Word/LibreOffice exportieren, inkl. Übersetzung\n"
        f'Exec="{venv_python}" -m ui.app\n'
        f"Path={app_source_dir}\n"
        f"{icon_line}\n"
        f"StartupWMClass={APP_WM_CLASS}\n"
        "Categories=Office;Network;\n"
        "Terminal=false\n"
    )


def create_linux_desktop_entry(
    app_source_dir: Path,
    venv_python: Path,
    icon_path: Path | None = None,
    *,
    display_name: str = APP_DISPLAY_NAME,
    entry_slug: str = APP_ENTRY_SLUG,
) -> Path:
    icon_path = _resolve_icon(app_source_dir, icon_path, "Linux")
    target_dir = linux_applications_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    entry_path = target_dir / f"{entry_slug}.desktop"
    entry_path.write_text(_linux_desktop_entry_content(app_source_dir, venv_python, icon_path, display_name))
    # Desktop-Dateien müssen für manche Launcher ausführbar sein, um vertraut zu werden.
    entry_path.chmod(entry_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return entry_path


# --- Windows -------------------------------------------------------------


def _windows_shortcut_script(
    shortcut_path: Path,
    venv_python: Path,
    app_source_dir: Path,
    icon_path: Path | None,
    display_name: str = APP_DISPLAY_NAME,
) -> str:
    icon_location = str(icon_path) if icon_path else str(venv_python)
    # WScript.Shell ist ein eingebautes Windows-COM-Objekt; kein Zusatzpaket
    # (z.B. pywin32) nur für diese eine .lnk-Datei nötig.
    return (
        "$WshShell = New-Object -ComObject WScript.Shell\n"
        f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
        f'$Shortcut.TargetPath = "{venv_python}"\n'
        '$Shortcut.Arguments = "-m ui.app"\n'
        f'$Shortcut.WorkingDirectory = "{app_source_dir}"\n'
        f'$Shortcut.IconLocation = "{icon_location}"\n'
        f'$Shortcut.Description = "{display_name}"\n'
        "$Shortcut.Save()\n"
    )


def create_windows_shortcut(
    app_source_dir: Path,
    venv_python: Path,
    icon_path: Path | None = None,
    *,
    display_name: str = APP_DISPLAY_NAME,
) -> Path:
    icon_path = _resolve_icon(app_source_dir, icon_path, "Windows")
    target_dir = windows_start_menu_programs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    shortcut_path = target_dir / f"{display_name}.lnk"
    script = _windows_shortcut_script(shortcut_path, venv_python, app_source_dir, icon_path, display_name)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=_WINDOWS_SHORTCUT_TIMEOUT_SECONDS,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopIntegrationError(f"Die Startmenü-Verknüpfung konnte nicht angelegt werden: {exc}") from exc
    return shortcut_path


# --- macOS -----------------------------------------------------------------


def _macos_launcher_script(venv_python: Path, app_source_dir: Path) -> str:
    return (
        "#!/bin/sh\n"
        f'cd "{app_source_dir}"\n'
        f'exec "{venv_python}" -m ui.app\n'
    )


def _macos_info_plist_content(bundle_name: str, icon_file: str | None = None) -> str:
    icon_entry = (
        f"    <key>CFBundleIconFile</key>\n    <string>{icon_file}</string>\n" if icon_file else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>CFBundleName</key>\n"
        f"    <string>{bundle_name}</string>\n"
        "    <key>CFBundleExecutable</key>\n"
        f"    <string>{bundle_name}</string>\n"
        "    <key>CFBundleIdentifier</key>\n"
        f"    <string>com.mistegit.{APP_ENTRY_SLUG}</string>\n"
        "    <key>CFBundlePackageType</key>\n"
        "    <string>APPL</string>\n"
        f"{icon_entry}"
        "    <key>CFBundleShortVersionString</key>\n"
        "    <string>1.0</string>\n"
        "    <key>LSMinimumSystemVersion</key>\n"
        "    <string>10.13</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def create_macos_app_bundle(
    app_source_dir: Path,
    venv_python: Path,
    icon_path: Path | None = None,
    *,
    display_name: str = APP_DISPLAY_NAME,
) -> Path:
    icon_path = _resolve_icon(app_source_dir, icon_path, "Darwin")
    bundle_path = macos_applications_dir() / f"{display_name}.app"
    macos_dir = bundle_path / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    launcher_path = macos_dir / display_name
    launcher_path.write_text(_macos_launcher_script(venv_python, app_source_dir))
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    icon_file: str | None = None
    if icon_path is not None and icon_path.is_file():
        resources_dir = bundle_path / "Contents" / "Resources"
        resources_dir.mkdir(parents=True, exist_ok=True)
        (resources_dir / icon_path.name).write_bytes(icon_path.read_bytes())
        icon_file = icon_path.name

    info_plist_path = bundle_path / "Contents" / "Info.plist"
    info_plist_path.write_text(_macos_info_plist_content(display_name, icon_file))

    return bundle_path


# --- Dispatch --------------------------------------------------------------


def create_desktop_entry(
    app_source_dir: Path,
    venv_python: Path,
    icon_path: Path | None = None,
    *,
    dev: bool = False,
) -> Path:
    """Verzweigt zum plattformpassenden Ersteller des Menüeintrags.

    dev=True schreibt den separaten Entwickler-Eintrag ("TME (dev)" /
    tme-dev.desktop), damit er nie eine vom Bootstrapper angelegte
    Installation auf derselben Maschine überschreibt.
    """
    display_name = DEV_DISPLAY_NAME if dev else APP_DISPLAY_NAME
    system = platform.system()
    if system == "Windows":
        return create_windows_shortcut(app_source_dir, venv_python, icon_path, display_name=display_name)
    if system == "Darwin":
        return create_macos_app_bundle(app_source_dir, venv_python, icon_path, display_name=display_name)
    if system == "Linux":
        return create_linux_desktop_entry(
            app_source_dir,
            venv_python,
            icon_path,
            display_name=display_name,
            entry_slug=DEV_ENTRY_SLUG if dev else APP_ENTRY_SLUG,
        )
    raise DesktopIntegrationError(f"Nicht unterstützte Plattform: {system!r}")


# --- Entwickler-CLI ----------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _can_import_app_deps(python: Path) -> bool:
    """True, wenn `python -m ui.app` mit diesem Interpreter zumindest an
    den Imports vorbeikäme (PySide6 ist die schwerste, am leichtesten
    fehlende Abhängigkeit)."""
    try:
        result = subprocess.run(
            [str(python), "-c", "import PySide6"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve_dev_python(python_override: str | None = None) -> Path:
    """Interpreter, den der Entwickler-Eintrag starten soll: --python falls
    angegeben, sonst der Interpreter, der diesen Befehl selbst ausführt -
    also das, worauf `python` in der Shell zeigt, aus der die App normalerweise
    gestartet wird (venv, pyenv, System-Python: alles in Ordnung, solange die
    Abhängigkeiten der App dort installiert sind)."""
    python = Path(python_override).expanduser().resolve() if python_override else Path(sys.executable).resolve()
    if not python.is_file():
        raise DesktopIntegrationError(f"Python-Interpreter nicht gefunden: {python}")
    if not _can_import_app_deps(python):
        print(
            f"Warnung: {python} kann PySide6 nicht importieren - der Eintrag startet die App erst, "
            "wenn die Abhängigkeiten für diesen Interpreter installiert sind, oder --python <pfad> "
            "auf einen mit vorhandenen Abhängigkeiten übergeben.",
            file=sys.stderr,
        )
    return python


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bootstrap.desktop_integration",
        description="Legt einen Anwendungsmenü-Eintrag für ein TME-Quellcode-Checkout an.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="schreibt den Entwickler-Eintrag (eigener Name, zeigt auf dieses Checkout und dessen venv)",
    )
    parser.add_argument("--python", help="zu startender Interpreter (Standard: der diesen Befehl ausführende)")
    parser.add_argument("--icon", help="zu verwendende Icon-Datei (Standard: assets/icon.<ext> dieses Checkouts)")
    args = parser.parse_args(argv)

    if not args.dev:
        parser.error("von der Kommandozeile aus wird nur --dev unterstützt; der Installations-Weg läuft über bootstrap/installer.py")

    try:
        python = resolve_dev_python(args.python)
        icon = Path(args.icon).expanduser().resolve() if args.icon else None
        entry = create_desktop_entry(repo_root(), python, icon, dev=True)
    except DesktopIntegrationError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(f"{entry} angelegt (startet {python})")
    print("Anwendungsmenü öffnen, Eintrag suchen und von dort an Taskleiste/Dock anheften.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
