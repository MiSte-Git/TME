"""
Ausnahmeliste für Emoji-Wörter, die NICHT übersetzt werden sollen (z.B.
Namen, feststehende Ausdrücke). Bewusst getrennt von letter_map.json (das
Buchstabe<->Custom-Emoji-Wörterbuch) gepflegt, aber im Aufbau analog:
JSON-Datei unter data/, optionaler CSV-Import/Export nach demselben Muster
wie lettermap_tools.py.

Vergleich in emoji_words.is_translatable() ist case-insensitiv (Wörter
werden hier so gespeichert, wie sie eingegeben wurden - Anzeige bleibt
lesbar -, der Abgleich normalisiert selbst).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Set
import csv
import json

NO_TRANSLATE_WORDS_FILE = Path("data/no_translate_words.json")


def load_no_translate_words(path: Path = NO_TRANSLATE_WORDS_FILE) -> List[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return sorted({str(w).strip() for w in data if str(w).strip()})


def load_no_translate_words_set(path: Path = NO_TRANSLATE_WORDS_FILE) -> Set[str]:
    return set(load_no_translate_words(path))


def save_no_translate_words(words: List[str], path: Path = NO_TRANSLATE_WORDS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = sorted({str(w).strip() for w in words if str(w).strip()})
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def add_word(word: str, path: Path = NO_TRANSLATE_WORDS_FILE) -> List[str]:
    words = set(load_no_translate_words(path))
    w = word.strip()
    if w:
        words.add(w)
    result = sorted(words)
    save_no_translate_words(result, path)
    return result


def remove_word(word: str, path: Path = NO_TRANSLATE_WORDS_FILE) -> List[str]:
    words = set(load_no_translate_words(path))
    words.discard(word.strip())
    result = sorted(words)
    save_no_translate_words(result, path)
    return result


def export_csv(out_csv: Path, path: Path = NO_TRANSLATE_WORDS_FILE) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    words = load_no_translate_words(path)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["word"])
        for word in words:
            writer.writerow([word])
    return out_csv


def import_csv(in_csv: Path, path: Path = NO_TRANSLATE_WORDS_FILE, merge: bool = True) -> List[str]:
    """Importiert Wörter aus einer CSV mit Spalte 'word'. merge=True ergänzt
    die bestehende Liste, merge=False ersetzt sie komplett."""
    words: Set[str] = set(load_no_translate_words(path)) if merge else set()
    if in_csv.exists():
        with in_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                w = (row.get("word") or "").strip()
                if w:
                    words.add(w)
    result = sorted(words)
    save_no_translate_words(result, path)
    return result
