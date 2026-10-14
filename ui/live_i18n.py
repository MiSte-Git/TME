"""Generischer Live-Sprachwechsel fuer Widgets ohne eigenes retranslate().

Problem: Qt uebersetzt bei einem Sprachwechsel (QEvent.LanguageChange) nichts
von selbst - jeder bereits gesetzte Text bleibt in der alten Sprache, bis er
neu gesetzt wird. Die aelteren Tabs loesen das mit handgeschriebenen
retranslate()-Methoden. Fuer Tabs mit vielen dynamischen Texten (Status-
zeilen, Zaehler "{selected} von {total} …", Platzhalter in Listen) waere das
fehleranfaellig, weil jeder Zustand einzeln nachgebaut werden muesste.

Loesung: LiveRetranslateMixin ueberschreibt tr() der Klasse. Jeder von tr()
gelieferte Text (und jedes Ergebnis von .format() darauf) wird in einer
Registry mit Quelltext und Format-Argumenten hinterlegt. Bei LanguageChange
werden alle Texte der Kind-Widgets gelesen; jeder in der Registry bekannte
Text wird in der neuen Sprache neu erzeugt und zurueckgeschrieben. Texte, die
nicht aus tr() stammen (Nachrichteninhalte, Fehlermeldungen von Telegram,
Links), sind nicht in der Registry und bleiben unveraendert.

Nutzung: class MyTab(LiveRetranslateMixin, QWidget) - Aufrufstellen bleiben
normales self.tr("…") bzw. self.tr("… {n}").format(n=…), lupdate findet sie
wie gewohnt. Kontext ist wie bei QObject.tr der Klassenname.

Grenzen: zusammengesetzte Texte (tr()-Text + Nachrichtentext per "+")
werden nicht erkannt und bleiben bis zum naechsten Setzen in der alten
Sprache. Offene modale Dialoge sind nicht betroffen (waehrend eines modalen
Dialogs ist die Sprachleiste ohnehin nicht bedienbar).
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget,
    QTabWidget, QWidget,
)

_MAX_ENTRIES = 5000


class _TrText(str):
    """str mit Herkunft (Kontext + Quelltext), damit .format() mitprotokolliert."""

    def __new__(cls, text: str, ctx: str, source: str, registry: "_Registry"):
        obj = super().__new__(cls, text)
        obj._ctx = ctx
        obj._source = source
        obj._registry = registry
        return obj

    def format(self, *args, **kwargs) -> str:  # type: ignore[override]
        out = str.format(self, *args, **kwargs)
        self._registry.remember(out, self._ctx, self._source, args, kwargs)
        return out

    def retranslated(self) -> "_TrText":
        text = QCoreApplication.translate(self._ctx, self._source)
        self._registry.remember(text, self._ctx, self._source, (), {})
        return _TrText(text, self._ctx, self._source, self._registry)


class _Registry:
    def __init__(self) -> None:
        self._entries: "OrderedDict[str, tuple[str, str, tuple, dict]]" = OrderedDict()

    def remember(self, text: str, ctx: str, source: str, args: tuple, kwargs: dict) -> None:
        if not text:
            return
        self._entries[text] = (ctx, source, args, kwargs)
        self._entries.move_to_end(text)
        while len(self._entries) > _MAX_ENTRIES:
            self._entries.popitem(last=False)

    def translate_current(self, text: str) -> str | None:
        """Text in der aktuellen Sprache neu erzeugen, None falls unbekannt."""
        entry = self._entries.get(text)
        if entry is None:
            return None
        ctx, source, args, kwargs = entry
        new = QCoreApplication.translate(ctx, source)
        if args or kwargs:
            args = tuple(a.retranslated() if isinstance(a, _TrText) else a for a in args)
            kwargs = {
                k: (v.retranslated() if isinstance(v, _TrText) else v)
                for k, v in kwargs.items()
            }
            try:
                new = new.format(*args, **kwargs)
            except (KeyError, IndexError, ValueError):
                return None
        self.remember(new, ctx, source, args, kwargs)
        return new


class LiveRetranslateMixin:
    """Mixin fuer QWidget-Unterklassen, siehe Moduldocstring."""

    def tr(self, source: str, disambiguation=None, n: int = -1) -> str:  # type: ignore[override]
        reg = self._live_registry()
        ctx = type(self).__name__
        text = QCoreApplication.translate(ctx, source, disambiguation, n)
        reg.remember(text, ctx, source, (), {})
        return _TrText(text, ctx, source, reg)

    def _live_registry(self) -> _Registry:
        reg = self.__dict__.get("_live_i18n_registry")
        if reg is None:
            reg = _Registry()
            self.__dict__["_live_i18n_registry"] = reg
        return reg

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt-Name)
        if event.type() == QEvent.Type.LanguageChange:
            self.live_retranslate()
        super().changeEvent(event)  # type: ignore[misc]

    def live_retranslate(self) -> None:
        reg = self._live_registry()

        def swap(getter, setter) -> None:
            old = getter()
            if old:
                new = reg.translate_current(old)
                if new is not None and new != old:
                    setter(new)

        widgets = [self, *self.findChildren(QWidget)]  # type: ignore[attr-defined]
        for w in widgets:
            swap(w.toolTip, w.setToolTip)
            if isinstance(w, (QLabel, QAbstractButton)):
                swap(w.text, w.setText)
            if isinstance(w, QGroupBox):
                swap(w.title, w.setTitle)
            if isinstance(w, QLineEdit):
                swap(w.placeholderText, w.setPlaceholderText)
            if isinstance(w, QComboBox):
                for i in range(w.count()):
                    swap(lambda i=i: w.itemText(i), lambda t, i=i: w.setItemText(i, t))
            if isinstance(w, QListWidget):
                for i in range(w.count()):
                    item = w.item(i)
                    swap(item.text, item.setText)
                    swap(item.toolTip, item.setToolTip)
            if isinstance(w, QTabWidget):
                for i in range(w.count()):
                    swap(lambda i=i: w.tabText(i), lambda t, i=i: w.setTabText(i, t))
