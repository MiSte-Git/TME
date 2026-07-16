# Agent guide for this repository

- Language/runtime: Python 3.11+ (tested up to 3.13). Layout: `pipeline/` and `ui/` are
  packages (core logic resp. PySide6 UI); root-level `.py` files (e.g. `schedule_json.py`)
  are standalone utility modules, not entry points.
- Entry points:
  - ui/app.py → Qt-based UI (Schedule-Tab, Lettermap-Tab); orchestrates the full
    schedule → ODT pipeline, incl. optional inline translations.
  - pipeline/emoji_pipeline.py → CLI for by-date, by-ids and lettermap-related workflows.
  - schedule_json.py (root) → utility module (read/write helpers for schedule files),
    imported by the entry points above; not runnable as a standalone CLI.
- Dependencies (install before running): python3 -m pip install -r requirements.txt
  (PySide6, PyYAML, lottie, odfpy, telethon, torch, openai-whisper, Pillow).
  OCR (pytesseract/easyocr) is planned but not implemented — not a current dependency.
- Build/run:
  - Syntax check (no deps): python3 -m compileall -q .
  - Run UI: python3 ui/app.py
  - Run CLI pipeline: python3 pipeline/emoji_pipeline.py <subcommand> ... (see README.md for examples)
- Tests/lint:
  - No tests configured in repo. If adding pytest: run all → pytest -q; single test → pytest -q path/to/test_file.py -k test_name
  - No linter/formatter configured. Prefer PEP 8; if adding tools, use ruff/black with default settings.
- Structure & data:
  - Input schedules: JSON schedule files; outputs: *.odt; media files saved under media/ and embedded into ODT.
  - Message ordering: by default, messages are grouped block-wise per schedule section
    (one H1 heading each, sorted chronologically only within that section). Set
    config.yaml's interleave_channels: true (or the UI checkbox) to merge messages
    from all sections/channels into a single chronologically sorted sequence instead;
    each message then carries a "Kanal: <name>" label in its header. See
    pipeline/message_collect.py's _merge_chronologically for the sort/tiebreak logic.
  - Telegram session persists in tg_session.session (created on first run).
  - Custom emoji mapping in custom_emoji_user_map.json; archive experiments in _archive/ (not part of runtime).
  - Incremental mode (config.yaml's incremental_mode: true, or the UI checkbox
    "Inkrementelles Update (Store)"): a persistent per-schedule message store
    lives under data/message_store/<schedule_stem>.json (pipeline/message_store.py),
    keyed by (channel_key, message_id) plus per-section fetch state (last
    message id/date per (channel, date, time-window) fingerprint). On each run,
    only new messages per section are fetched (Telethon min_id, threaded through
    message_filters.fetch_messages_for_section_day); the output document is
    always fully re-rendered from the store (render_records_from_store), never
    appended - no timestamp in the filename in this mode, the same file is
    overwritten. Corrupted/unreadable store files are backed up (*.corrupt-*)
    and replaced with an empty store rather than crashing.
- External APIs:
  - Telethon (Telegram): messages.TranslateTextRequest for translations; GetCustomEmojiDocumentsRequest; optional channels.JoinChannelRequest.
  - ODT generation via odfpy (styles, TOC, images). OCR via Tesseract/EasyOCR is planned
    (see README.md Roadmap) but not implemented yet.
  - Pluggable translation providers (pipeline/translation/): "telegram" (default, wraps
    the Telethon call above, no extra API key) plus deepl/google/chatgpt, each a plain
    REST call via stdlib urllib (no SDK dependency added). Selected via config.yaml's
    translation.provider, --provider on the CLI, or the UI dropdown. deepl/google use
    API-native tag-preserving translation modes; chatgpt relies on a prompt instruction
    instead (best-effort, no hard guarantee) - see pipeline/translation/formatting.py
    and base.py docstrings for the mask/unmask design and why "telegram" isn't a
    TranslationProvider instance itself (it needs peer/message-id context, not just text).
- Code style:
  - PEP 8 naming (snake_case funcs; UPPER_CASE constants); stdlib imports first, then third-party.
  - Prefer small, local changes; keep German user-facing messages as-is; handle timezones with zoneinfo.
  - Error handling follows current pattern: catch-and-continue with print diagnostics; avoid raising unless input is invalid.
- Tooling rules: No Cursor/Claude/Windsurf/Cline/Goose/Copilot instruction files present in repo.
