"""tkinter-Oberfläche für den Bootstrapper-Assistenten.

Dünne Widget-Schicht über bootstrap/controller.py::BootstrapController, der
den gesamten eigentlichen Zustand/die Logik hält (siehe dessen Docstring für
die Begründung des Splits). Dieses Modul ist bewusst nicht direkt
unit-getestet; seine Korrektheit wird stattdessen über den PyInstaller-Build
je Betriebssystem in CI geprüft (.github/workflows/build-bootstrap.yml).

Lang laufende Arbeit (vor allem die Installation selbst - venv-Erstellung,
pip-Installationen, ein Netzwerk-Download) läuft in einem Hintergrund-Thread,
damit das Fenster reaktionsfähig bleibt; Ergebnisse werden über eine mit
Tks eigenem `after()` abgefragte queue.Queue an den Hauptthread zurückgegeben
- der Standard-Weg, tkinter-Widgets sicher von außerhalb des Hauptthreads zu
beeinflussen (tkinter selbst ist nicht thread-sicher).
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from bootstrap.controller import BootstrapController
from bootstrap.installer import InstallMode, InstallProgress, InstallStep

_WINDOW_SIZE = "640x520"

# bootstrap.install_step_*-Textkatalog-Schlüssel, je InstallStep.
_INSTALL_STEP_KEYS = {
    InstallStep.SOURCE: "bootstrap.install_step_source",
    InstallStep.VENV: "bootstrap.install_step_venv",
    InstallStep.DEPS: "bootstrap.install_step_deps",
    InstallStep.SHORTCUT: "bootstrap.install_step_shortcut",
}


class BootstrapApp(tk.Tk):
    def __init__(self, controller: BootstrapController | None = None) -> None:
        super().__init__()
        self.controller = controller if controller is not None else BootstrapController()
        self.geometry(_WINDOW_SIZE)
        self.title(self.controller.text("bootstrap.window_title"))

        self._container = ttk.Frame(self, padding=16)
        self._container.pack(fill="both", expand=True)
        self._current_frame: ttk.Frame | None = None

        self._selected_providers: dict[str, tk.BooleanVar] = {}
        self._provider_queue: list[str] = []

        self._show_welcome()

    # --- Frame-Hilfsfunktionen ----------------------------------------

    def _set_frame(self, build: Callable[[ttk.Frame], None]) -> None:
        if self._current_frame is not None:
            self._current_frame.destroy()
        frame = ttk.Frame(self._container)
        frame.pack(fill="both", expand=True)
        self._current_frame = frame
        build(frame)

    def _nav_row(self, frame: ttk.Frame, back: Callable[[], None] | None, next_: Callable[[], None] | None, next_label: str | None = None) -> None:
        row = ttk.Frame(frame)
        row.pack(side="bottom", fill="x", pady=(16, 0))
        if back is not None:
            ttk.Button(row, text=self.controller.text("bootstrap.back_button"), command=back).pack(side="left")
        if next_ is not None:
            label = next_label if next_label is not None else self.controller.text("bootstrap.next_button")
            ttk.Button(row, text=label, command=next_).pack(side="right")

    # --- Schritt 1: Willkommen / Sprache ------------------------------

    def _show_welcome(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.welcome_title"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(frame, text=self.controller.text("bootstrap.welcome_text"), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 16)
            )

            lang_row = ttk.Frame(frame)
            lang_row.pack(anchor="w")
            ttk.Label(lang_row, text=self.controller.text("bootstrap.language_label") + ":").pack(side="left")
            lang_var = tk.StringVar(value=self.controller.language)

            def on_language_change(*_args: object) -> None:
                self.controller.set_language(lang_var.get())
                self._show_welcome()  # neu aufbauen, damit jedes Label in der neuen Sprache erscheint

            combo = ttk.Combobox(lang_row, textvariable=lang_var, values=["de", "en"], state="readonly", width=6)
            combo.pack(side="left", padx=(8, 0))
            combo.bind("<<ComboboxSelected>>", on_language_change)

            self._nav_row(frame, back=None, next_=self._show_mode)

        self._set_frame(build)

    # --- Schritt 2: Modus (Standard / mit Transkription) ---------------

    def _show_mode(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.mode_title"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(frame, text=self.controller.text("bootstrap.mode_intro"), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 16)
            )

            mode_var = tk.StringVar(
                value=(self.controller.mode.value if self.controller.mode else InstallMode.STANDARD.value)
            )

            standard_frame = ttk.Frame(frame)
            standard_frame.pack(anchor="w", fill="x", pady=(0, 12))
            ttk.Radiobutton(
                standard_frame, text=self.controller.text("bootstrap.mode_standard_label"), variable=mode_var, value=InstallMode.STANDARD.value
            ).pack(anchor="w")
            ttk.Label(standard_frame, text=self.controller.text("bootstrap.mode_standard_desc"), wraplength=560, justify="left").pack(
                anchor="w", padx=(24, 0)
            )

            stt_frame = ttk.Frame(frame)
            stt_frame.pack(anchor="w", fill="x")
            ttk.Radiobutton(
                stt_frame, text=self.controller.text("bootstrap.mode_stt_label"), variable=mode_var, value=InstallMode.WITH_STT.value
            ).pack(anchor="w")
            ttk.Label(stt_frame, text=self.controller.text("bootstrap.mode_stt_desc"), wraplength=560, justify="left").pack(
                anchor="w", padx=(24, 0)
            )

            def on_next() -> None:
                self.controller.set_mode(InstallMode(mode_var.get()))
                self._show_install()

            self._nav_row(frame, back=self._show_welcome, next_=on_next)

        self._set_frame(build)

    # --- Schritt 3: Installation ----------------------------------------

    def _show_install(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.install_title"), font=("", 16, "bold")).pack(anchor="w")
            status_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=status_var, wraplength=560, justify="left").pack(anchor="w", pady=(8, 16))
            progress = ttk.Progressbar(frame, mode="indeterminate")
            progress.pack(fill="x", pady=(0, 16))
            progress.start(12)

            result_queue: queue.Queue = queue.Queue()

            def on_progress(step_progress: InstallProgress) -> None:
                key = _INSTALL_STEP_KEYS[step_progress.step]
                text = self.controller.text(key, name=step_progress.detail) if step_progress.detail else self.controller.text(key)
                result_queue.put(("progress", text))

            def worker() -> None:
                try:
                    self.controller.run_install(progress_cb=on_progress)
                    self.controller.write_language_marker()
                    result_queue.put(("done", None))
                except Exception as exc:  # noqa: BLE001 - wird dem Nutzer 1:1 angezeigt
                    result_queue.put(("error", str(exc)))

            threading.Thread(target=worker, daemon=True).start()

            def poll() -> None:
                try:
                    while True:
                        kind, payload = result_queue.get_nowait()
                        if kind == "progress":
                            status_var.set(payload)
                        elif kind == "done":
                            progress.stop()
                            self._show_telegram_credentials()
                            return
                        elif kind == "error":
                            progress.stop()
                            self._show_install_failed(payload)
                            return
                except queue.Empty:
                    pass
                self.after(150, poll)

            self.after(150, poll)

        self._set_frame(build)

    def _show_install_failed(self, error_message: str) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.install_failed_title"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(
                frame,
                text=self.controller.text("bootstrap.install_failed", error=error_message),
                wraplength=560,
                justify="left",
            ).pack(anchor="w", pady=(8, 16))
            self._nav_row(frame, back=self._show_mode, next_=None)

        self._set_frame(build)

    # --- Schritt 4a: Zugangsdaten - Telegram ----------------------------

    def _show_telegram_credentials(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.credentials_telegram_label"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(frame, text=self.controller.text("bootstrap.credentials_telegram_explain"), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 12)
            )
            ttk.Button(
                frame,
                text=self.controller.text("bootstrap.credentials_open_signup_button"),
                command=self.controller.open_telegram_signup_page,
            ).pack(anchor="w", pady=(0, 12))

            form = ttk.Frame(frame)
            form.pack(anchor="w", fill="x")

            api_id_var = tk.StringVar(value="")
            api_hash_var = tk.StringVar(value="")
            phone_var = tk.StringVar(value="")

            ttk.Label(form, text=self.controller.text("bootstrap.credentials_telegram_api_id_label") + ":").grid(row=0, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=api_id_var, width=40).grid(row=0, column=1, sticky="w", pady=2)
            ttk.Label(form, text=self.controller.text("bootstrap.credentials_telegram_api_hash_label") + ":").grid(row=1, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=api_hash_var, width=40, show="*").grid(row=1, column=1, sticky="w", pady=2)
            ttk.Label(form, text=self.controller.text("bootstrap.credentials_telegram_phone_label") + ":").grid(row=2, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=phone_var, width=40).grid(row=2, column=1, sticky="w", pady=2)

            status_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=status_var, wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

            def on_save() -> None:
                api_id = api_id_var.get().strip()
                api_hash = api_hash_var.get().strip()
                phone = phone_var.get().strip() or None
                if not api_id or not api_hash:
                    return
                try:
                    self.controller.save_telegram_credentials(api_id, api_hash, phone)
                except ValueError:
                    status_var.set(self.controller.text("bootstrap.credentials_telegram_api_id_label") + ": ungültig")
                    return
                status_var.set(self.controller.text("bootstrap.credentials_saved"))

            row = ttk.Frame(frame)
            row.pack(side="bottom", fill="x", pady=(16, 0))
            ttk.Button(row, text=self.controller.text("bootstrap.credentials_skip_button"), command=self._show_provider_checklist).pack(side="left")
            ttk.Button(row, text=self.controller.text("bootstrap.credentials_save_button"), command=on_save).pack(side="right", padx=(8, 0))
            ttk.Button(row, text=self.controller.text("bootstrap.credentials_continue_button"), command=self._show_provider_checklist).pack(side="right")

        self._set_frame(build)

    # --- Schritt 4b: Zugangsdaten - Übersetzungs-Provider ---------------

    def _show_provider_checklist(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.credentials_title"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(frame, text=self.controller.text("bootstrap.credentials_provider_intro"), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 16)
            )

            self._selected_providers = {}
            for provider in self.controller.list_providers():
                var = tk.BooleanVar(value=False)
                self._selected_providers[provider] = var
                label_key = f"bootstrap.credentials_provider_{provider}"
                ttk.Checkbutton(frame, text=self.controller.text(label_key), variable=var).pack(anchor="w")

            def on_continue() -> None:
                self._provider_queue = [p for p, v in self._selected_providers.items() if v.get()]
                self._show_next_provider_or_finish()

            row = ttk.Frame(frame)
            row.pack(side="bottom", fill="x", pady=(16, 0))
            ttk.Button(row, text=self.controller.text("bootstrap.credentials_skip_all_button"), command=self._show_finish).pack(
                side="left"
            )
            ttk.Button(row, text=self.controller.text("bootstrap.credentials_continue_button"), command=on_continue).pack(
                side="right"
            )

        self._set_frame(build)

    def _show_next_provider_or_finish(self) -> None:
        if not self._provider_queue:
            self._show_finish()
            return
        provider = self._provider_queue.pop(0)
        self._show_single_provider(provider)

    def _show_single_provider(self, provider: str) -> None:
        def build(frame: ttk.Frame) -> None:
            provider_label = self.controller.text(f"bootstrap.credentials_provider_{provider}")
            ttk.Label(frame, text=provider_label, font=("", 16, "bold")).pack(anchor="w")
            explain_key = f"bootstrap.credentials_explain_{provider}"
            ttk.Label(frame, text=self.controller.text(explain_key), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 16)
            )
            ttk.Button(
                frame,
                text=self.controller.text("bootstrap.credentials_open_signup_button"),
                command=lambda: self.controller.open_signup_page(provider),
            ).pack(anchor="w", pady=(0, 16))

            ttk.Label(frame, text=self.controller.text("bootstrap.credentials_key_label", provider=provider_label)).pack(
                anchor="w"
            )
            key_var = tk.StringVar(value="")
            entry = ttk.Entry(frame, textvariable=key_var, show="*", width=50)
            entry.pack(anchor="w", pady=(4, 8))
            status_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=status_var, wraplength=560, justify="left").pack(anchor="w")

            def on_save() -> None:
                value = key_var.get().strip()
                if not value:
                    return
                target = self.controller.save_provider_credential(provider, value)
                if target == "keyring":
                    status_var.set(self.controller.text("bootstrap.credentials_saved"))
                else:
                    status_var.set(self.controller.text("bootstrap.credentials_saved_plaintext_warning"))

            ttk.Button(frame, text=self.controller.text("bootstrap.credentials_save_button"), command=on_save).pack(
                anchor="w", pady=(0, 16)
            )

            self._nav_row(
                frame,
                back=None,
                next_=self._show_next_provider_or_finish,
                next_label=self.controller.text("bootstrap.credentials_continue_button"),
            )

        self._set_frame(build)

    # --- Schritt 5: Fertig ------------------------------------------------

    def _show_finish(self) -> None:
        def build(frame: ttk.Frame) -> None:
            ttk.Label(frame, text=self.controller.text("bootstrap.finish_title"), font=("", 16, "bold")).pack(anchor="w")
            ttk.Label(frame, text=self.controller.text("bootstrap.finish_text"), wraplength=560, justify="left").pack(
                anchor="w", pady=(8, 16)
            )

            def on_launch() -> None:
                self.controller.launch_app()
                self.destroy()

            row = ttk.Frame(frame)
            row.pack(side="bottom", fill="x", pady=(16, 0))
            ttk.Button(row, text=self.controller.text("bootstrap.finish_close_button"), command=self.destroy).pack(side="left")
            ttk.Button(row, text=self.controller.text("bootstrap.finish_launch_button"), command=on_launch).pack(side="right")

        self._set_frame(build)


def main() -> None:
    app = BootstrapApp()
    app.mainloop()


if __name__ == "__main__":
    main()
