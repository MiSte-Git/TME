"""Utility helpers to convert generated ODT files into DOCX."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

class DocxConversionError(RuntimeError):
    """Raised when DOCX conversion fails."""


def _lookup_libreoffice() -> tuple[str, list[str]] | None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if exe:
        return "libreoffice", [exe, "--headless"]
    return None


def _lookup_pandoc() -> tuple[str, list[str]] | None:
    exe = shutil.which("pandoc")
    if exe:
        return "pandoc", [exe]
    return None


def _which_tool(prefer: str | None = None) -> tuple[str, list[str]]:
    """Return (tool_name, base_command)."""
    prefer_norm = (prefer or "").strip().lower() or None
    if prefer_norm == "libreoffice":
        choice = _lookup_libreoffice() or _lookup_pandoc()
    elif prefer_norm == "pandoc":
        choice = _lookup_pandoc() or _lookup_libreoffice()
    else:
        choice = _lookup_libreoffice() or _lookup_pandoc()
    if not choice:
        raise DocxConversionError(
            "Kein Konvertierungstool gefunden. Bitte LibreOffice (soffice) oder Pandoc installieren."
        )
    return choice


def convert_odt_to_docx(
    odt_path: Path,
    outdir: Path | None = None,
    prefer: str | None = None,
    reference_docx: Path | None = None,
) -> Path:
    """
    Convert the given ODT file into DOCX and return the resulting path.

    prefer: "libreoffice" | "pandoc" | None (auto)
    reference_docx: only used when pandoc is selected.
    """
    odt_path = Path(odt_path).expanduser().resolve()
    if not odt_path.exists():
        raise FileNotFoundError(odt_path)

    destination_dir = Path(outdir or odt_path.parent).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    docx_path = destination_dir / f"{odt_path.stem}.docx"

    tool, base_cmd = _which_tool(prefer)
    if tool == "libreoffice":
        cmd = base_cmd + ["--convert-to", "docx", "--outdir", str(destination_dir), str(odt_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not docx_path.exists():
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise DocxConversionError(f"LibreOffice-Konvertierung fehlgeschlagen: {detail}")
        return docx_path

    # Pandoc
    cmd = base_cmd + [str(odt_path), "-o", str(docx_path)]
    if reference_docx is not None:
        ref_path = Path(reference_docx).expanduser().resolve()
        if not ref_path.exists():
            raise DocxConversionError(f"Pandoc-Referenzdatei nicht gefunden: {ref_path}")
        cmd += ["--reference-doc", str(ref_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not docx_path.exists():
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise DocxConversionError(f"Pandoc-Konvertierung fehlgeschlagen: {detail}")
    return docx_path


__all__ = ["convert_odt_to_docx", "DocxConversionError"]

