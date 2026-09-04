"""Geführter, laienfreundlicher Installer für TME (der "Bootstrapper").

Eigenständiges tkinter-Programm - bewusst NICHT Teil von ui/ (PySide6) und
importiert nichts von dort. Begründung (analog zum bereits produktiven
Vorbild in PDF-Translator, siehe dessen bootstrap/__init__.py-Docstring):
wer die eigentliche App noch nicht installiert hat, kann kein PySide6-
Programm starten, um PySide6 zu installieren - dieses Paket hängt deshalb
nur von der Python-Standardbibliothek plus dem bereits optionalen 'keyring'-
Paket ab (siehe credentials.py), beides installiert bevor requirements.txt
der eigentlichen App geholt wird. Jedes Untermodul hier muss diese
Einschränkung einhalten: kein Import von PySide6, requests, oder irgendetwas
aus ui/ oder pipeline/ (außer den unten genannten, bewusst leichtgewichtigen
Ausnahmen) irgendwo in diesem Paket.

Einstiegspunkt: `python -m bootstrap` (siehe bootstrap/__main__.py) oder die
per PyInstaller gebaute Executable aus
.github/workflows/build-bootstrap.yml.

Verhältnis zum bestehenden Vollbuild (TME.spec/build_win.ps1/build_linux.sh/
TME_mac.spec): dieser Bootstrapper ersetzt den bestehenden Weg NICHT,
sondern existiert als zweite, parallele Installationsmöglichkeit (04.09.2026,
Michael: "Parallel anbieten"). Der Vollbuild bleibt unverändert bestehen.
"""
