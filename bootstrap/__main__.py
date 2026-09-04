"""Einstiegspunkt für `python -m bootstrap` und für den PyInstaller-Build
(.github/workflows/build-bootstrap.yml zeigt mit --name/Entry-Skript hierher).
"""
from __future__ import annotations

from bootstrap.app import main

if __name__ == "__main__":
    main()
