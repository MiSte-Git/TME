Ja. Option B passt. Hier ist das kompakte Gesamtkonzept inkl. JSON-Schemas.
„ODT-Style-IDs“ = die **Stil-Namen** (Absatz/Zeichen/Grafik) im ODT, auf die dein Exporter verweist, statt jedes Mal harte Formatwerte zu setzen.

# Architektur (PySide6, ohne Code)

* UI: Tabs für Eingabe, Assets, Original-Vorschau, Letter-Map, Übersetzung, Export.
* Worker: Threads für Telegram-Fetch, Rendering (WEBM/TGS→PNG), ODT-Schreiben.
* Kernmodule:

  1. **fetch**: Nachrichten+Entities holen
  2. **assets**: `document_id → PNG` rendern + Metadaten
  3. **runs**: Telegram-Text → `TextRun|EmojiRun|LineBreak`
  4. **odt\_writer**: Runs → ODT (as-char Frames für Emojis)
  5. **lettermap**: A–Z/0–9/Satzzeichen kuratieren
  6. **translate**: Plaintext erzeugen, Zieltext einlesen
  7. **report**: Lücken/Fallbacks/Fehler

# Projektstruktur

```
config.yaml
cache/emoji/<doc_id>.png
data/messages/<chat>_<msg_id>.json
data/assets.json
data/emoji_sets.json
data/runs.original/<chat>_<msg_id>.json
data/letter_map.json
data/plain/<chat>_<msg_id>.txt
data/translated/<chat>_<msg_id>.txt
data/runs.translated/<chat>_<msg_id>.json
out/original/<chat>_<msg_id>.odt
out/translated/<chat>_<msg_id>.odt
out/report.json
```

# Pipeline (mehrere Nachrichten, vollautomatisch)

1. **collect**: Liste von URLs/Kanal+Range → `messages/*.json`
2. **index/render**: neue `document_id`s → PNG in `cache/emoji/` + `assets.json` aktualisieren
3. **build runs (original)**: pro Nachricht → `runs.original/*.json`
4. **write ODT (original)**: pro Nachricht oder Sammel-ODT
5. **letter-map autosuggest**: Vorschlag aus Dateinamen/Set → `letter_map.json` prüfen/ergänzen
6. **extract plaintext**: `EmojiRun`→Buchstabe via invertierter Map → `plain/*.txt`
7. **translate**: `translated/*.txt` befüllen
8. **recompose runs (translated)**: Zieltext → `runs.translated/*.json`
9. **write ODT (translated)**
10. **report**: fehlende Zeichen, Fallbacks, Rendering-Fehler

# JSON-Schemas (präzise, aber knapp)

## `data/messages/<chat>_<msg_id>.json`

```json
{
  "chat": "QuantumStellarInitiative",
  "message_id": 50604,
  "date_iso": "2025-09-18T08:30:00Z",
  "text": "T H E …",
  "entities": [
    {"type":"custom_emoji","offset":0,"length":1,"document_id":"7551469895452344321"},
    {"type":"bold","offset":2,"length":5},
    {"type":"link","offset":20,"length":10,"url":"https://…"}
  ]
}
```

* `offset/length` sind UTF-16-Indexing wie von Telegram.

## `data/assets.json`

```json
{
  "7551469895452344321": {
    "file": "cache/emoji/7551469895452344321.png",
    "w": 512, "h": 512, "mime": "image/png",
    "set_id": "sticker_set_123",        // optional
    "set_title": "Fancy Letters",       // optional
    "orig_name": "AnimatedSticker.tgs", // wie geliefert
    "letter_hint": "R"                  // Heuristik aus Name/Alt
  },
  "…": { "file": "cache/emoji/…", "w": 512, "h": 512, "mime":"image/png" }
}
```

## `data/emoji_sets.json` (optional, wenn Set abrufbar)

```json
{
  "sticker_set_123": {
    "title": "Fancy Letters",
    "short_name": "fancy_letters",
    "doc_ids": ["7551469895452344321", "…"]
  }
}
```

## `data/runs.original/<chat>_<msg_id>.json`

```json
{
  "chat": "QuantumStellarInitiative",
  "message_id": 50604,
  "runs": [
    {"kind":"EmojiRun","document_id":"7551469895452344321","height_em":1.1},
    {"kind":"TextRun","text":"HE "},
    {"kind":"EmojiRun","document_id":"7551469…","height_em":1.1},
    {"kind":"LineBreak"},
    {"kind":"TextRun","text":"Weiterer Text…"}
  ]
}
```

## `data/letter_map.json`

```json
{
  "A": {"document_id":"111"},
  "B": {"document_id":"112"},
  "C": {"document_id":"113"},
  "0": {"document_id":"901"},
  "!": {"document_id":"801"},
  "space": " ",             // explizit erlaubt
  "fallback": "text",       // "text" | "skip"
  "case_mode": "upper"      // "upper" | "preserve"
}
```

## `data/plain/<chat>_<msg_id>.txt`

Reiner Text, gewonnen aus Original-Runs mittels inverse Map `{doc_id → Letter}`.

## `data/translated/<chat>_<msg_id>.txt`

Zieltext, manuell oder extern erzeugt.

## `data/runs.translated/<chat>_<msg_id>.json`

```json
{
  "chat": "QuantumStellarInitiative",
  "message_id": 50604,
  "runs": [
    {"kind":"EmojiRun","document_id":"111","height_em":1.1},   // "A"
    {"kind":"EmojiRun","document_id":"115","height_em":1.1},   // "B"
    {"kind":"TextRun","text":" und "},
    {"kind":"EmojiRun","document_id":"201","height_em":1.1}    // "Z"
  ],
  "meta": {"missing_chars":["Ä","Ö"], "fallback_count":2}
}
```

## `out/report.json`

```json
{
  "stats": {
    "messages": 42,
    "assets_rendered": 128,
    "assets_cached": 560,
    "tgs_failed": 1
  },
  "missing_letters": ["Q","X","Ä","Ö"],
  "fallbacks": {
    "text_used": 7,
    "skipped": 0
  },
  "errors": []
}
```

# ODT-Style-IDs (was, warum, wie)

**ODF/ODT arbeitet mit benannten Stilen.** Dein Exporter referenziert nur die **Namen**, die einmalig in `styles.xml` definiert werden.

Empfohlene Minimal-Stile:

* **Absatzstile (`style:family="paragraph"`)**

  * `P.Base` → Fließtext (Font, Größe, Zeilenhöhe)
  * `P.EmojiLine` → wenn eine Zeile überwiegend Emojis enthält (gleiche Werte wie Basis; optional)
* **Zeichenstile (`style:family="text"`)**

  * `T.Base` → Normaltext
  * `T.Bold`, `T.Italic`, `T.Link` → für Entity-Formatierungen
* **Grafikstile (`style:family="graphic"`)**

  * `G.InlineEmoji` → für `draw:frame` + `draw:image` der Emoji-PNGs
    Wichtige Properties: `style:run-through="foreground"`, `style:wrap="none"`, `svg:height` kommt **pro Instanz** aus `height_em` (Exporter rechnet em→pt→cm), `text:anchor-type="as-char"` auf dem Frame.
* **Seitenstil (`style:family="page"`)**

  * `Page.Default` → Seitenränder, Seitengröße

**Mapping Run → ODT**

* `TextRun` → `text:span` mit `text:style-name="T.Base"` innerhalb `text:p style-name="P.Base"`.
* `EmojiRun` → `draw:frame text:anchor-type="as-char" style-name="G.InlineEmoji"` mit `svg:height` aus `height_em` und `svg:width` proportional (aus PNG-Meta). Darin `draw:image xlink:href="Pictures/<doc_id>.png"`.
* `LineBreak` → `text:line-break` oder neuer Absatz `text:p`.

**Warum Style-IDs:** Konsistente Optik, kleine ODTs, einfache Wartung. Änderungen an Schriftgröße/Zeilenhöhe einmal im Stil, nicht in jedem Element.

# Integration in dein bestehendes ODT-Skript

* **Vorhanden:** Formatierter ODT-Export für Text.
* **Ergänzen:**

  1. **Runs-Ebene einziehen:** Baue aus Telegram-Text+Entities eine Sequenz aus `TextRun|EmojiRun|LineBreak` (wie oben).
  2. **Emoji-Rendering nutzen:** Vor dem ODT-Schreiben sicherstellen, dass für jede `document_id` eine PNG in `cache/emoji/` liegt (`assets.json`).
  3. **as-char-Frames einfügen:** An den Offsets der `EmojiRun`s statt Text ein `draw:frame` mit `G.InlineEmoji` einfügen.
  4. **Stile anlegen:** Einmalig `P.Base`, `T.*`, `G.InlineEmoji`, `Page.Default` in `styles.xml` definieren; Exporter verweist nur noch auf Namen.
  5. **Deduplizieren:** Bilder nur einmal unter `Pictures/<doc_id>.png`, mehrfach referenzieren.

# Automatisierung & Skalierung

* Globaler Cache nach `document_id`.
* Batch über viele Nachrichten: `collect` kann Kanal paginieren; `index/render` dedupliziert.
* Letter-Map einmal kuratieren, dann vollautomatisch Rekomposition.
* Report liefert Lücken; UI zeigt fehlende Zeichen.

