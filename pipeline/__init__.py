"""
Pipeline-Module gemäß docs/emoji-odt-kontext.md

Module:
- fetch: Telegram-Nachrichten und Entities einsammeln → data/messages/*.json
- assets: Custom-Emoji (document_id) rendern/cachen → cache/emoji + data/assets.json
- runs: Text + Entities → Runs (TextRun/EmojiRun/LineBreak) → data/runs.original/*.json
- odt_writer: Runs → ODT mit Style-IDs
- lettermap: Mapping A–Z/0–9/Satzzeichen → document_id, sowie inverse Map
- translate: Plaintext extrahieren, Zieltexte einlesen → data/plain/*.txt, data/translated/*.txt
- report: Statistiken, Fehler, Lücken → out/report.json

Adapters:
- adapters.existing_scripts: Wrapper um bestehende Skripte, ohne sie zu verändern
"""
