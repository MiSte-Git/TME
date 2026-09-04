"""Holt den App-Code (ui/, pipeline/, requirements*.txt, credentials.py, ...)
für Stufe 2 des Installers.

Lädt ein versioniertes GitHub-Release-ZIP-Asset herunter statt den Code in
den Bootstrapper selbst einzubetten oder per `git clone` zu holen (bräuchte
ein installiertes/erreichbares git und würde die volle Repo-Historie
mitziehen) - Vorgehen 1:1 vom bereits produktiven Vorbild in PDF-Translator
übernommen (siehe dessen bootstrap/release_source.py-Docstring). Nutzt nur
urllib.request aus der Standardbibliothek statt `requests`, damit dieses
Modul schon nutzbar ist, bevor requirements.txt installiert ist (siehe
Modul-Docstring von bootstrap/__init__.py).

Lokaler Dev-Override: die Umgebungsvariable TME_BOOTSTRAP_SOURCE auf ein
lokales Verzeichnis oder eine lokale .zip-Datei gesetzt lässt
download_app_source() das statt eines GitHub-Kontakts verwenden - zum Testen
des Bootstrappers selbst, kein für Endnutzer:innen gedachtes Feature.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

REPO_OWNER = "MiSte-Git"
REPO_NAME = "TME"

GITHUB_API_LATEST_RELEASE = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)

DEV_SOURCE_ENV_VAR = "TME_BOOTSTRAP_SOURCE"

_REQUEST_TIMEOUT_SECONDS = 30
_DOWNLOAD_CHUNK_SIZE = 1 << 16  # 64 KiB

# Die GitHub-API verlangt bei jeder Anfrage einen User-Agent-Header, sonst
# antwortet sie mit 403; jeder nicht-leere Wert wird akzeptiert.
_USER_AGENT = "tme-bootstrapper"

ProgressCallback = Callable[[int, Optional[int]], None]


class ReleaseSourceError(RuntimeError):
    """Wird geworfen, wenn der App-Code nicht aufgelöst/heruntergeladen
    werden konnte."""


def fetch_latest_release_metadata() -> dict:
    """GitHub-API-JSON für das aktuellste Release des Repos."""
    request = urllib.request.Request(
        GITHUB_API_LATEST_RELEASE, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReleaseSourceError(f"GitHub konnte nicht erreicht werden: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseSourceError(f"GitHub lieferte eine unerwartete Antwort: {exc}") from exc


def resolve_zip_asset_url(release_metadata: dict) -> str:
    """browser_download_url des ersten .zip-Assets in den Assets eines
    Release."""
    for asset in release_metadata.get("assets", []):
        name = asset.get("name", "")
        url = asset.get("browser_download_url")
        if url and name.lower().endswith(".zip"):
            return url
    raise ReleaseSourceError(
        "Das aktuellste GitHub-Release hat kein .zip-Asset angehängt."
    )


def _download_to_file(url: str, dest: Path, progress_cb: ProgressCallback | None = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            total = response.length  # None, falls der Server kein Content-Length sendet
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReleaseSourceError(f"Download fehlgeschlagen: {exc}") from exc


def _extract_zip_flattened(zip_path: Path, dest_dir: Path) -> None:
    """Entpackt `zip_path` nach `dest_dir` und löst dabei ein einzelnes
    oberstes Verzeichnis auf, falls vorhanden - GitHubs automatisch erzeugte
    Release-/Source-ZIPs wickeln immer alles in einen einzelnen
    "<repo>-<ref>/"-Ordner, den Aufrufer:innen von app_source_dir() nicht
    kennen müssen sollten."""
    with tempfile.TemporaryDirectory(prefix="tme-extract-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        entries = list(tmp_path.iterdir())
        source_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in source_root.iterdir():
            target = dest_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))


def _copy_dev_source(source: Path, dest_dir: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, dest_dir, dirs_exist_ok=True)
    elif source.is_file() and source.suffix.lower() == ".zip":
        _extract_zip_flattened(source, dest_dir)
    else:
        raise ReleaseSourceError(
            f"{DEV_SOURCE_ENV_VAR}={source} ist weder ein Verzeichnis noch eine .zip-Datei."
        )


def download_app_source(
    dest_dir: Path,
    dev_source_override: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    """Befüllt `dest_dir` mit dem App-Code und gibt es zurück.

    `dev_source_override` fällt bei None auf die Umgebungsvariable
    TME_BOOTSTRAP_SOURCE zurück, passend zum lokalen Dev-Override im
    Modul-Docstring; in Tests einen expliziten Wert (auch "") übergeben, um
    unabhängig von der aufrufenden Umgebung zu bleiben.
    """
    override = dev_source_override if dev_source_override is not None else os.environ.get(DEV_SOURCE_ENV_VAR)
    if override:
        _copy_dev_source(Path(override).expanduser(), dest_dir)
        return dest_dir

    metadata = fetch_latest_release_metadata()
    zip_url = resolve_zip_asset_url(metadata)
    with tempfile.TemporaryDirectory(prefix="tme-download-") as tmp:
        zip_path = Path(tmp) / "release.zip"
        _download_to_file(zip_url, zip_path, progress_cb)
        _extract_zip_flattened(zip_path, dest_dir)
    return dest_dir
