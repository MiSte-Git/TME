"""Sicheres Kopieren und optionales Löschen von Telegram-Topic-Nachrichten.

Ein kompletter Topic-Verlauf wird nie gelöscht. Löschbar sind ausschließlich
einzeln markierte, erneut geladene Nachrichten aus Quelle oder Ziel.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from telethon import TelegramClient, errors, types
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ForwardMessagesRequest

from credentials import get_telegram_credentials
from pipeline.telegram_login import SESSION_NAME

ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]
RUNS_DIR = Path("data/topic_copy_runs")
CACHE_DIR = Path("data/topic_copy_cache")
COPY_BATCH_SIZE = 100
COMMAND_RE = re.compile(
    r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?=\s|$)", re.IGNORECASE,
)


class TopicCopyCancelled(Exception):
    pass


@dataclass(frozen=True)
class TopicReference:
    peer: str | int
    topic_id: int


@dataclass(frozen=True)
class TopicMessageInfo:
    message_id: int
    sender_id: int | None
    sender_name: str
    text: str
    is_bot: bool
    reply_to_id: int | None
    date_iso: str = ""
    original_date_iso: str = ""
    is_bot_command: bool = False


@dataclass(frozen=True)
class BotGroup:
    bot_id: int
    bot_name: str
    request_ids: tuple[int, ...]
    response_ids: tuple[int, ...]

    @property
    def message_ids(self) -> tuple[int, ...]:
        return self.request_ids + self.response_ids


@dataclass(frozen=True)
class TopicPreview:
    messages: tuple[TopicMessageInfo, ...]
    bot_groups: tuple[BotGroup, ...]
    unassigned_command_ids: tuple[int, ...]
    topic_title: str = ""
    target_title: str = ""


@dataclass(frozen=True)
class TopicCopyResult:
    found: int
    copied: int
    run_file: Path
    source_title: str = ""
    target_title: str = ""


@dataclass(frozen=True)
class TopicResumeResult:
    total: int
    previously_copied: int
    copied_now: int
    remaining: int
    run_file: Path
    uncopyable: int = 0


@dataclass(frozen=True)
class DeletionCandidate:
    source_id: int
    target_id: int
    sender_name: str
    text: str
    date_iso: str = ""
    target_date_iso: str = ""
    original_date_iso: str = ""
    possible_duplicate: bool = False


@dataclass(frozen=True)
class RollbackAuditItem:
    source_id: int
    target_id: int
    sender_name: str
    text: str
    date_iso: str
    target_date_iso: str
    category: str
    reason: str = ""
    bot_id: int | None = None
    bot_name: str = ""


@dataclass(frozen=True)
class RollbackAudit:
    run_file: Path
    safe: tuple[RollbackAuditItem, ...]
    ambiguous: tuple[RollbackAuditItem, ...]
    missing: tuple[RollbackAuditItem, ...]
    wrong_topic: tuple[RollbackAuditItem, ...]
    uncopyable: tuple[RollbackAuditItem, ...]
    already_deleted: tuple[RollbackAuditItem, ...]


def parse_topic_link(value: str) -> TopicReference:
    raw = value.strip()
    if not raw:
        raise ValueError("Bitte einen Topic-Link eingeben.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        raise ValueError("Bitte einen gültigen t.me-Topic-Link verwenden.")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] == "s":
        parts = parts[1:]
    if len(parts) >= 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
        return TopicReference(int("-100" + parts[1]), int(parts[2]))
    if len(parts) >= 2 and parts[0] not in {"c", "joinchat"} and parts[1].isdigit():
        return TopicReference(parts[0], int(parts[1]))
    raise ValueError("Topic nicht erkannt. Bitte den Link direkt im Topic-Menü kopieren.")


async def _authorized_client() -> TelegramClient:
    api_id, api_hash, _phone = get_telegram_credentials()
    client = TelegramClient(
        SESSION_NAME, api_id, api_hash,
        # Genau ein kontrollierter Wiederholungsversuch: Er ist nötig, damit
        # Telethon nach einer kurzen Flood-Wait-Pause (z. B. GetRepliesRequest)
        # denselben Leseaufruf fortsetzt. Mehrfache unsichtbare Wiederholungen
        # grosser Kopierblöcke bleiben damit ausgeschlossen.
        request_retries=1, timeout=30, auto_reconnect=True,
        # Sonst ersetzt Telethon die eigentliche Server-/Timeout-Ursache nach
        # allen Versuchen durch das wenig hilfreiche "Request was unsuccessful".
        raise_last_call_error=True,
        # Reine Lesevorgänge dürfen moderate Telegram-Drosselungen automatisch
        # abwarten. Unmittelbar vor ForwardMessages wird diese Grenze auf zehn
        # Sekunden abgesenkt, damit Kopieren niemals lange unsichtbar wartet.
        flood_sleep_threshold=60,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telegram-Session ungültig oder abgelaufen. Bitte zuerst über "
            "'Jetzt einloggen' im Telegram-Export anmelden."
        )
    return client


async def _validate_topic(client: TelegramClient, peer, topic_id: int) -> str:
    # GetForumTopicsByIDRequest ist nicht in allen von Telethon 1.41.x
    # ausgelieferten API-Layern vorhanden. Die Topic-Startnachricht ist dagegen
    # layerübergreifend abrufbar und hat bei normalen Topics die ID des Topics.
    root_message = await client.get_messages(peer, ids=topic_id)
    if root_message is None:
        raise ValueError(
            f"Topic-ID {topic_id} wurde in der angegebenen Gruppe nicht gefunden."
        )
    action = getattr(root_message, "action", None)
    topic_action_type = getattr(types, "MessageActionTopicCreate", None)
    if topic_id != 1 and topic_action_type is not None and not isinstance(
        action, topic_action_type,
    ):
        raise ValueError(
            f"Die ID {topic_id} gehört nicht zum Beginn eines Forum-Topics. "
            "Bitte den Link direkt im Topic-Menü kopieren."
        )
    title = getattr(action, "title", None)
    if title:
        return str(title)
    return "Allgemein" if topic_id == 1 else f"Topic {topic_id}"


def _sender_name(sender, fallback: int | None) -> str:
    if sender is None:
        return f"Unbekannt ({fallback})" if fallback else "Unbekannt"
    title = getattr(sender, "title", None)
    if title:
        return str(title)
    name = " ".join(filter(None, (
        getattr(sender, "first_name", None), getattr(sender, "last_name", None),
    ))).strip()
    username = getattr(sender, "username", None)
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else f"ID {fallback}")


async def _load_messages(
    client: TelegramClient,
    topic: TopicReference,
    progress: Optional[ProgressCallback] = None,
    use_cache: bool = False,
):
    peer = await client.get_input_entity(topic.peer)
    topic_title = await _validate_topic(client, peer, topic.topic_id)
    raw_messages = []
    infos: list[TopicMessageInfo] = []
    cache_path = None
    if use_cache:
        cache_key = hashlib.sha256(
            f"{topic.peer}:{topic.topic_id}".encode("utf-8")
        ).hexdigest()[:24]
        cache_path = CACHE_DIR / f"{cache_key}.json"
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            for item in cached.get("messages") or []:
                infos.append(TopicMessageInfo(**item))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            infos = []
    cached_max_id = max((info.message_id for info in infos), default=0)
    if progress and infos:
        progress(len(infos), 0)
    sender_cache = {}
    async for message in client.iter_messages(
        peer, reply_to=topic.topic_id, reverse=True, min_id=cached_max_id,
    ):
        if getattr(message, "action", None) is not None:
            continue
        sender_id = getattr(message, "sender_id", None)
        sender = sender_cache.get(sender_id)
        if sender_id is not None and sender is None:
            try:
                sender = await message.get_sender()
            except Exception:
                sender = None
            if sender is not None:
                sender_cache[sender_id] = sender
        reply = getattr(message, "reply_to", None)
        fwd = getattr(message, "fwd_from", None)
        original_date = getattr(fwd, "date", None)
        infos.append(TopicMessageInfo(
            message_id=message.id,
            sender_id=sender_id,
            sender_name=_sender_name(sender, sender_id),
            text=(getattr(message, "message", None) or "").strip(),
            is_bot=bool(getattr(sender, "bot", False)),
            reply_to_id=getattr(reply, "reply_to_msg_id", None),
            date_iso=(
                message.date.isoformat()
                if getattr(message, "date", None) is not None
                else ""
            ),
            original_date_iso=(
                original_date.isoformat() if original_date is not None else ""
            ),
            is_bot_command=any(
                isinstance(entity, types.MessageEntityBotCommand)
                and getattr(entity, "offset", -1) == 0
                for entity in (getattr(message, "entities", None) or [])
            ),
        ))
        raw_messages.append(message)
        if progress and len(infos) % 100 == 0:
            progress(len(infos), 0)
    if progress:
        progress(len(infos), 0)
    if cache_path is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "peer": str(topic.peer),
            "topic_id": topic.topic_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "messages": [asdict(info) for info in infos],
        }, ensure_ascii=False), encoding="utf-8")
    return peer, raw_messages, tuple(infos), topic_title


def build_preview(messages: tuple[TopicMessageInfo, ...]) -> TopicPreview:
    bots = {m.sender_id: m for m in messages if m.is_bot and m.sender_id is not None}
    requests = {bot_id: set() for bot_id in bots}
    responses = {bot_id: set() for bot_id in bots}
    command_ids = set()
    command_names = {}
    username_to_id = {}
    for bot_id, bot in bots.items():
        match = re.search(r"@([A-Za-z0-9_]+)", bot.sender_name)
        if match:
            username_to_id[match.group(1).casefold()] = bot_id
    for message in messages:
        if message.is_bot and message.sender_id in responses:
            responses[message.sender_id].add(message.message_id)
        match = COMMAND_RE.match(message.text)
        if match and message.is_bot_command and not message.is_bot:
            command_ids.add(message.message_id)
            command_names[message.message_id] = match.group(1).casefold()
            username = match.group(2)
            if username and username.casefold() in username_to_id:
                requests[username_to_id[username.casefold()]].add(message.message_id)
    # Direkte Antworten ordnen auch /befehle ohne @BotName zuverlässig zu.
    for message in messages:
        if message.is_bot and message.sender_id in requests and message.reply_to_id in command_ids:
            requests[message.sender_id].add(message.reply_to_id)
    # Aus sicheren Treffern lernen: Wenn derselbe Befehl bereits per @Botname
    # oder direkter Antwort genau einem Bot zugeordnet wurde, gilt dies auch
    # für weitere Telegram-Befehle desselben Namens.
    command_to_bots: dict[str, set[int]] = {}
    for bot_id, request_ids in requests.items():
        for request_id in request_ids:
            command_to_bots.setdefault(command_names[request_id], set()).add(bot_id)
    assigned = set().union(*requests.values()) if requests else set()
    for message_id in command_ids - assigned:
        matching_bots = command_to_bots.get(command_names[message_id], set())
        if len(matching_bots) == 1:
            requests[next(iter(matching_bots))].add(message_id)
    # Ist im geladenen Topic nur ein einziger Bot vorhanden, ist ein von
    # Telegram ausdrücklich als BotCommand markierter Befehl ebenfalls eindeutig.
    assigned = set().union(*requests.values()) if requests else set()
    if len(bots) == 1:
        only_bot_id = next(iter(bots))
        requests[only_bot_id].update(command_ids - assigned)
    assigned = set().union(*requests.values()) if requests else set()
    groups = tuple(
        BotGroup(
            bot_id=bot_id,
            bot_name=bot.sender_name,
            request_ids=tuple(sorted(requests[bot_id])),
            response_ids=tuple(sorted(responses[bot_id])),
        )
        for bot_id, bot in sorted(bots.items(), key=lambda item: item[1].sender_name.casefold())
    )
    return TopicPreview(
        messages=messages,
        bot_groups=groups,
        unassigned_command_ids=tuple(sorted(command_ids - assigned)),
    )


async def inspect_topic(
    source_link: str,
    target_link: str = "",
    progress: Optional[ProgressCallback] = None,
) -> TopicPreview:
    source = parse_topic_link(source_link)
    client = await _authorized_client()
    try:
        _peer, _raw, infos, source_title = await _load_messages(
            client, source, progress, use_cache=True,
        )
        preview = build_preview(infos)
        target_title = ""
        if target_link.strip():
            target = parse_topic_link(target_link)
            target_peer = await client.get_input_entity(target.peer)
            target_title = await _validate_topic(client, target_peer, target.topic_id)
        return TopicPreview(
            messages=preview.messages,
            bot_groups=preview.bot_groups,
            unassigned_command_ids=preview.unassigned_command_ids,
            topic_title=source_title,
            target_title=target_title,
        )
    finally:
        await client.disconnect()


def _forwarded_messages(result) -> list[object]:
    found = []
    for update in getattr(result, "updates", None) or []:
        message = getattr(update, "message", None)
        if message is not None and getattr(message, "id", None) is not None:
            found.append(message)
    return sorted(found, key=lambda message: message.id)


def _message_topic_id(message) -> int | None:
    """Ermittelt das Forum-Topic einer Nachricht aus Telegrams Reply-Header."""
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None
    return (
        getattr(reply, "reply_to_top_id", None)
        or getattr(reply, "reply_to_msg_id", None)
    )


def _belongs_to_topic(message, topic_id: int) -> bool:
    actual_topic_id = _message_topic_id(message)
    # Nachrichten im allgemeinen Topic können ohne Reply-Header erscheinen.
    return actual_topic_id == topic_id or (topic_id == 1 and actual_topic_id is None)


def _normalized_iso(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return value


def _source_match_key(info: dict) -> tuple[str, str]:
    return (
        (info.get("text") or "").strip(),
        _normalized_iso(info.get("original_date_iso") or info.get("date_iso") or ""),
    )


def _target_match_key(message) -> tuple[str, str]:
    fwd = getattr(message, "fwd_from", None)
    original_date = getattr(fwd, "date", None)
    return (
        (getattr(message, "message", None) or "").strip(),
        _normalized_iso(original_date.isoformat() if original_date else ""),
    )


def _archive_invalid_mapping(
    data: dict, mapping: dict, message, reason: str,
) -> None:
    archived = dict(mapping)
    archived.update({
        "reason": reason,
        "actual_topic_id": _message_topic_id(message) if message is not None else None,
        "invalidated_at": datetime.now(timezone.utc).isoformat(),
    })
    data.setdefault("invalidated_mappings", []).append(archived)


async def _revalidate_recorded_mappings(
    client: TelegramClient,
    run_file: Path,
    data: dict,
    target_peer,
    target_topic_id: int,
    cancel_event: threading.Event | None = None,
    status: Optional[StatusCallback] = None,
) -> int:
    """Behält nur Ziel-IDs, die Telegram weiterhin dem Ziel-Topic zuordnet."""
    mappings = list(data.get("mappings") or [])
    validated_ids = {int(value) for value in data.get("validated_target_ids") or []}
    if not validated_ids:
        previous = data.get("last_target_revalidation") or {}
        if previous.get("invalid") == 0 and int(previous.get("checked") or 0) > 0:
            # Migration alter Ledgers: Ziel-IDs wachsen chronologisch. Alles,
            # was bei der letzten vollständigen Prüfung bereits vorhanden war,
            # bilden daher die kleinsten damals geprüften Ziel-IDs.
            checked = int(previous["checked"])
            validated_ids.update(sorted(
                int(item["target_id"]) for item in mappings
            )[:checked])
    valid = [
        mapping for mapping in mappings
        if int(mapping["target_id"]) in validated_ids
    ]
    unchecked = [
        mapping for mapping in mappings
        if int(mapping["target_id"]) not in validated_ids
    ]
    invalid_count = 0
    for start in range(0, len(unchecked), 100):
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                "wurde gespeichert und kann später fortgesetzt werden."
            )
        batch = unchecked[start:start + 100]
        if status:
            status(
                f"Gespeicherte Zielkopien werden geprüft: "
                f"{start} von {len(unchecked)}…"
            )
        messages = await client.get_messages(
            target_peer, ids=[int(item["target_id"]) for item in batch],
        )
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        messages = list(messages) + [None] * (len(batch) - len(messages))
        for mapping, message in zip(batch, messages):
            expected_id = int(mapping["target_id"])
            exists = message is not None and getattr(message, "id", None) == expected_id
            if exists and _belongs_to_topic(message, target_topic_id):
                valid.append(mapping)
                validated_ids.add(expected_id)
                continue
            reason = "wrong_topic" if exists else "missing_target_message"
            _archive_invalid_mapping(data, mapping, message, reason)
            invalid_count += 1
    data["mappings"] = valid
    data["validated_target_ids"] = sorted(
        int(mapping["target_id"]) for mapping in valid
    )
    data["last_target_revalidation"] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(unchecked),
        "valid": len(valid),
        "invalid": invalid_count,
    }
    _write_run(data, run_file)
    return invalid_count


def _begin_pending_batch(data: dict, run_file: Path, source_ids: list[int]) -> None:
    data["pending_batch"] = {
        "source_ids": source_ids,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "known_target_max_id": max(
            (int(item["target_id"]) for item in data.get("mappings") or []),
            default=0,
        ),
    }
    _write_run(data, run_file)


def _finish_pending_batch(data: dict) -> None:
    pending = data.pop("pending_batch", None)
    if pending:
        pending["finished_at"] = datetime.now(timezone.utc).isoformat()
        data.setdefault("resolved_pending_batches", []).append(pending)


async def _resolve_pending_batch(
    client: TelegramClient,
    run_file: Path,
    data: dict,
    target_peer,
    target_topic_id: int,
    cancel_event: threading.Event | None = None,
    status: Optional[StatusCallback] = None,
) -> int:
    """Gleicht einen Block ab, dessen Telegram-Antwort verloren ging."""
    pending = data.get("pending_batch")
    if not pending:
        return 0
    source_ids = [int(value) for value in pending.get("source_ids") or []]
    known_sources = {
        int(item["source_id"]) for item in data.get("mappings") or []
    }
    source_ids = [source_id for source_id in source_ids if source_id not in known_sources]
    if not source_ids:
        _finish_pending_batch(data)
        _write_run(data, run_file)
        return 0
    started_at = datetime.fromisoformat(pending["started_at"])
    min_target_id = int(pending.get("known_target_max_id") or 0)
    me = await client.get_me()
    candidates = []
    scanned = 0
    async for message in _iter_messages_after_id(
        client, target_peer, min_target_id, cancel_event,
    ):
        scanned += 1
        if status and (scanned == 1 or scanned % 100 == 0):
            status(
                f"Ausstehender Telegram-Block wird abgeglichen: "
                f"{scanned} Zielnachrichten geprüft…"
            )
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                "wurde gespeichert und kann später fortgesetzt werden."
            )
        if (
            message.date
            and message.date >= started_at
            and getattr(message, "sender_id", None) == me.id
            and _belongs_to_topic(message, target_topic_id)
        ):
            candidates.append(message)
    infos = data.get("messages") or {}
    source_by_key: dict[tuple[str, str], list[int]] = {}
    for source_id in source_ids:
        key = _source_match_key(infos.get(str(source_id), {}))
        source_by_key.setdefault(key, []).append(source_id)
    target_by_key: dict[tuple[str, str], list[object]] = {}
    for message in candidates:
        key = _target_match_key(message)
        if key in source_by_key:
            target_by_key.setdefault(key, []).append(message)
    recovered = 0
    mapped_target_ids = {
        int(item["target_id"]) for item in data.get("mappings") or []
    }
    for key, matching_sources in source_by_key.items():
        matching_targets = sorted(
            target_by_key.get(key, []), key=lambda message: message.id,
        )
        for source_id, message in zip(sorted(matching_sources), matching_targets):
            if message.id not in mapped_target_ids:
                data["mappings"].append({
                    "source_id": source_id, "target_id": message.id,
                })
                data.setdefault("validated_target_ids", []).append(message.id)
                mapped_target_ids.add(message.id)
                recovered += 1
        for message in matching_targets[len(matching_sources):]:
            data.setdefault("possible_duplicate_deliveries", []).append({
                "target_id": message.id,
                "text": key[0],
                "detected_at": datetime.now(timezone.utc).isoformat(),
            })
    pending["recovered"] = recovered
    pending["possible_duplicates"] = max(0, len(candidates) - recovered)
    _finish_pending_batch(data)
    data["mappings"].sort(key=lambda item: int(item["source_id"]))
    _write_run(data, run_file)
    return recovered


def _prepare_forward_request(client: TelegramClient) -> None:
    """Schaltet automatische Wiederholungen ausschließlich fürs Senden aus."""
    client._request_retries = 0
    client.flood_sleep_threshold = 10


def _reduced_batch_size(current: int) -> int:
    """Verkleinert Telegram-Blöcke, ohne unter fünf Nachrichten zu fallen."""
    return max(5, int(current) // 2)


async def _record_unsupported_messages(
    client: TelegramClient,
    source_peer,
    source_ids: list[int],
    data: dict,
    run_file: Path,
) -> set[int]:
    """Protokolliert Medien, die Telegram nicht weiterleiten kann."""
    if not source_ids:
        return set()
    messages = await client.get_messages(source_peer, ids=source_ids)
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    unsupported = {
        int(message.id)
        for message in messages
        if message is not None
        and isinstance(getattr(message, "media", None), types.MessageMediaUnsupported)
    }
    if not unsupported:
        return set()
    recorded = {int(value) for value in data.get("uncopyable_source_ids") or []}
    new_ids = unsupported - recorded
    data["uncopyable_source_ids"] = sorted(recorded | unsupported)
    for source_id in sorted(new_ids):
        data.setdefault("uncopyable_messages", []).append({
            "source_id": source_id,
            "reason": "message_media_unsupported",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })
    _write_run(data, run_file)
    return unsupported


async def _interruptible_wait(
    seconds: int, cancel_event: threading.Event | None,
) -> None:
    for _ in range(max(0, seconds)):
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                "wurde gespeichert und kann später fortgesetzt werden."
            )
        await asyncio.sleep(1)


async def _iter_messages_after_id(
    client: TelegramClient,
    peer,
    min_id: int,
    cancel_event: threading.Event | None = None,
):
    """Lädt nur neue Gruppen-IDs direkt, ohne das Topic zu durchblättern."""
    latest = await client.get_messages(peer, limit=1)
    if not latest:
        return
    max_id = int(latest[0].id)
    for start in range(int(min_id) + 1, max_id + 1, 100):
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                "wurde gespeichert und kann später fortgesetzt werden."
            )
        end = min(start + 99, max_id)
        messages = await client.get_messages(peer, ids=list(range(start, end + 1)))
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            if message is not None:
                yield message


async def _wait_for_flood_limit(
    seconds: int,
    cancel_event: threading.Event | None,
    status: Optional[StatusCallback],
    confirmed: int,
    total: int,
) -> None:
    """Wartet Telegrams Sperrzeit ab und hält die UI per Countdown aktuell."""
    seconds = max(1, int(seconds))
    resume_at = datetime.now().astimezone() + timedelta(seconds=seconds)
    for remaining in range(seconds, 0, -1):
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                "wurde gespeichert und kann später fortgesetzt werden."
            )
        if status and (
            remaining == seconds or remaining % 60 == 0 or remaining <= 10
        ):
            status(
                f"Telegram-Pause: {confirmed} bestätigt, "
                f"{max(0, total - confirmed)} noch offen. Noch "
                f"{remaining} Sekunden; automatische Fortsetzung um "
                f"{resume_at.strftime('%H:%M:%S')} Uhr…"
            )
        await asyncio.sleep(1)


async def _wait_for_operation_limit(
    seconds: int,
    cancel_event: threading.Event | None,
    status: Optional[StatusCallback],
    operation: str,
) -> None:
    seconds = max(1, int(seconds))
    resume_at = datetime.now().astimezone() + timedelta(seconds=seconds)
    for remaining in range(seconds, 0, -1):
        if cancel_event is not None and cancel_event.is_set():
            raise TopicCopyCancelled(
                "Vorgang abgebrochen. Bereits bestätigte Schritte sind gespeichert."
            )
        if status and (
            remaining == seconds or remaining % 60 == 0 or remaining <= 10
        ):
            status(
                f"Telegram-Pause beim {operation}: noch {remaining} Sekunden; "
                f"automatische Fortsetzung um {resume_at.strftime('%H:%M:%S')} Uhr…"
            )
        await asyncio.sleep(1)


async def _get_messages_with_wait(
    client: TelegramClient,
    peer,
    ids: list[int],
    cancel_event: threading.Event | None,
    status: Optional[StatusCallback],
):
    while True:
        try:
            return await client.get_messages(peer, ids=ids)
        except FloodWaitError as exc:
            await _wait_for_operation_limit(
                int(exc.seconds), cancel_event, status, "Prüfen",
            )


def _write_run(data: dict, path: Path | None = None) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = RUNS_DIR / f"topic-copy-{stamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def copy_topic_messages(
    source_link: str,
    target_link: str,
    *,
    excluded_bot_ids: set[int] | None = None,
    selected_source_ids: set[int] | None = None,
    silent: bool = True,
    progress: Optional[ProgressCallback] = None,
    cancel_event: threading.Event | None = None,
) -> TopicCopyResult:
    source = parse_topic_link(source_link)
    target = parse_topic_link(target_link)
    if source == target:
        raise ValueError("Quell- und Ziel-Topic sind identisch.")
    client = await _authorized_client()
    run_data = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_link": source_link,
        "target_link": target_link,
        "complete": False,
        "mappings": [],
        "messages": {},
    }
    run_path = _write_run(run_data)
    try:
        source_peer, _raw, infos, source_title = await _load_messages(client, source)
        preview = build_preview(infos)
        excluded_ids = set()
        if selected_source_ids is None:
            for group in preview.bot_groups:
                if group.bot_id in (excluded_bot_ids or set()):
                    excluded_ids.update(group.message_ids)
        selected = [
            message for message in infos
            if message.message_id not in excluded_ids
            and (
                selected_source_ids is None
                or message.message_id in selected_source_ids
            )
        ]
        target_peer = await client.get_input_entity(target.peer)
        if source_peer == target_peer and source.topic_id == target.topic_id:
            raise ValueError("Quell- und Ziel-Topic sind identisch.")
        target_title = await _validate_topic(client, target_peer, target.topic_id)
        run_data["messages"] = {str(m.message_id): asdict(m) for m in selected}
        _write_run(run_data, run_path)
        total = len(selected)
        if progress:
            progress(0, total)
        _prepare_forward_request(client)
        for start in range(0, total, COPY_BATCH_SIZE):
            if cancel_event is not None and cancel_event.is_set():
                raise TopicCopyCancelled(
                    "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                    "wurde gespeichert."
                )
            source_ids = [
                m.message_id for m in selected[start:start + COPY_BATCH_SIZE]
            ]
            _begin_pending_batch(run_data, run_path, source_ids)
            while True:
                try:
                    result = await client(ForwardMessagesRequest(
                        from_peer=source_peer,
                        id=source_ids,
                        to_peer=target_peer,
                        silent=silent,
                        # Telethon 1.41/1.42 serialisiert reply_to bei diesem
                        # Request zwar, Telegram ordnet die Weiterleitung damit
                        # aber nicht dem Forum-Topic zu. top_msg_id ist hier das
                        # kompatible, tatsächlich ausgewertete Feld.
                        top_msg_id=target.topic_id,
                    ))
                    break
                except FloodWaitError as exc:
                    raise RuntimeError(
                        "Telegram verlangt eine Wartezeit von "
                        f"{exc.seconds} Sekunden. Der Kopiervorgang wurde sicher "
                        "angehalten; der bestätigte Zwischenstand bleibt gespeichert."
                    ) from exc
            forwarded = _forwarded_messages(result)
            if len(forwarded) != len(source_ids):
                raise RuntimeError(
                    "Telegram hat nicht jede Zielnachricht eindeutig bestätigt. "
                    "Der ausstehende Block wird vor dem Fortsetzen abgeglichen."
                )
            misdirected = 0
            for source_id, target_message in zip(source_ids, forwarded):
                mapping = {
                    "source_id": source_id, "target_id": target_message.id,
                }
                if _belongs_to_topic(target_message, target.topic_id):
                    run_data["mappings"].append(mapping)
                    run_data.setdefault("validated_target_ids", []).append(
                        target_message.id
                    )
                else:
                    _archive_invalid_mapping(
                        run_data, mapping, target_message, "wrong_topic",
                    )
                    misdirected += 1
            _finish_pending_batch(run_data)
            _write_run(run_data, run_path)
            if progress:
                progress(len(run_data["mappings"]), total)
            if misdirected:
                raise RuntimeError(
                    f"Telegram hat {misdirected} Nachricht(en) nicht dem "
                    "Ziel-Topic zugeordnet. Sie zählen nicht als bestätigt; "
                    "der Kopiervorgang wurde sicher angehalten."
                )
        run_data["complete"] = len(run_data["mappings"]) == total
        _write_run(run_data, run_path)
        return TopicCopyResult(
            total, len(run_data["mappings"]), run_path,
            source_title, target_title,
        )
    finally:
        await client.disconnect()


def latest_complete_run() -> Path | None:
    if not RUNS_DIR.exists():
        return None
    for path in sorted(RUNS_DIR.glob("topic-copy-*.json"), reverse=True):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("complete"):
                return path
        except Exception:
            continue
    return None


def latest_incomplete_run() -> Path | None:
    if not RUNS_DIR.exists():
        return None
    for path in sorted(RUNS_DIR.glob("topic-copy-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("complete") and data.get("messages"):
                return path
        except Exception:
            continue
    return None


async def _reconcile_run(
    client: TelegramClient,
    run_file: Path,
    data: dict,
    cancel_event: threading.Event | None = None,
    status: Optional[StatusCallback] = None,
) -> int:
    """Rekonstruiert Kopien nach einem verlorenen ForwardMessages-Resultat."""
    target = parse_topic_link(data["target_link"])
    target_peer = await client.get_input_entity(target.peer)
    await _validate_topic(client, target_peer, target.topic_id)
    created_at = datetime.fromisoformat(data["created_at"])
    last_scanned_id = int(data.get("last_reconcile_target_max_id") or 0)
    max_seen_id = last_scanned_id
    known_targets = {int(item["target_id"]) for item in data.get("mappings") or []}
    mapped_sources = {int(item["source_id"]) for item in data.get("mappings") or []}
    by_key: dict[tuple[str, str], list[int]] = {}
    for source_id, info in (data.get("messages") or {}).items():
        sid = int(source_id)
        if sid not in mapped_sources:
            by_key.setdefault(_source_match_key(info), []).append(sid)
    me = await client.get_me()
    target_by_key: dict[tuple[str, str], list[object]] = {}
    old_retries = client._request_retries
    old_threshold = client.flood_sleep_threshold
    client._request_retries = 1
    client.flood_sleep_threshold = 60
    scanned = 0
    try:
        if last_scanned_id:
            message_iterator = _iter_messages_after_id(
                client, target_peer, last_scanned_id, cancel_event,
            )
        else:
            message_iterator = client.iter_messages(
                target_peer, reply_to=target.topic_id,
            )
        async for message in message_iterator:
            scanned += 1
            if cancel_event is not None and cancel_event.is_set():
                raise TopicCopyCancelled(
                    "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                    "wurde gespeichert und kann später fortgesetzt werden."
                )
            max_seen_id = max(max_seen_id, int(message.id))
            if (
                not last_scanned_id
                and (not message.date or message.date < created_at)
            ):
                break
            if (
                _belongs_to_topic(message, target.topic_id)
                and (last_scanned_id or (message.date and message.date >= created_at))
                and message.id not in known_targets
                and getattr(message, "sender_id", None) == me.id
            ):
                key = _target_match_key(message)
                if key in by_key:
                    target_by_key.setdefault(key, []).append(message)
            if status and (scanned == 1 or scanned % 100 == 0):
                possible = sum(
                    min(len(by_key.get(key, ())), len(messages))
                    for key, messages in target_by_key.items()
                )
                status(
                    f"Zielabgleich läuft: {scanned} Nachrichten geprüft, "
                    f"{possible} passende neue Kopien gefunden…"
                )
    finally:
        client._request_retries = old_retries
        client.flood_sleep_threshold = old_threshold
    added = 0
    for key, source_ids in by_key.items():
        target_messages = sorted(
            target_by_key.get(key, []), key=lambda message: message.id,
        )
        source_ids = sorted(source_ids)
        for source_id, message in zip(source_ids, target_messages):
            data["mappings"].append({
                "source_id": source_id, "target_id": message.id,
            })
            data.setdefault("validated_target_ids", []).append(message.id)
            mapped_sources.add(source_id)
            added += 1
        if len(target_messages) > len(source_ids):
            recorded = {int(item.get("target_id", -1)) for item in (
                data.get("possible_duplicate_deliveries") or []
            )}
            for message in target_messages[len(source_ids):]:
                if message.id not in recorded:
                    data.setdefault("possible_duplicate_deliveries", []).append({
                        "target_id": message.id,
                        "text": key[0],
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    })
    data["last_reconcile_target_max_id"] = max_seen_id
    data["validated_target_ids"] = sorted(set(
        int(value) for value in data.get("validated_target_ids") or []
    ))
    if added or max_seen_id != last_scanned_id or data.get("possible_duplicate_deliveries"):
        data["mappings"].sort(key=lambda item: int(item["source_id"]))
        _write_run(data, run_file)
    return added


async def resume_topic_copy(
    run_file: Path,
    progress: Optional[ProgressCallback] = None,
    cancel_event: threading.Event | None = None,
    status: Optional[StatusCallback] = None,
) -> TopicResumeResult:
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if data.get("complete"):
        raise ValueError("Dieser Kopiervorgang ist bereits vollständig.")
    selected_ids = [int(value) for value in (data.get("messages") or {})]
    selected_set = set(selected_ids)

    def confirmed_count() -> int:
        mapped_sources = {
            int(item["source_id"])
            for item in data.get("mappings") or []
        }
        return len(mapped_sources & selected_set)

    def emit_progress() -> None:
        if progress:
            progress(confirmed_count(), len(selected_ids))

    emit_progress()
    client = await _authorized_client()
    try:
        source = parse_topic_link(data["source_link"])
        target = parse_topic_link(data["target_link"])
        source_peer = await client.get_input_entity(source.peer)
        target_peer = await client.get_input_entity(target.peer)
        await _validate_topic(client, source_peer, source.topic_id)
        await _validate_topic(client, target_peer, target.topic_id)
        # Alte API-Ergebnisse sind erst nach dieser erneuten Serverprüfung
        # bestätigt. Fehlgeleitete Kopien (z. B. zurück ins Quell-Topic) werden
        # aus dem aktiven Ledger entfernt, bleiben aber im Audit erhalten.
        if status:
            status("Gespeicherte Zielnachrichten werden bei Telegram geprüft…")
        await _revalidate_recorded_mappings(
            client, run_file, data, target_peer, target.topic_id,
            cancel_event, status,
        )
        emit_progress()
        pending_source_ids = [
            int(value)
            for value in (data.get("pending_batch") or {}).get("source_ids") or []
        ]
        recovered_pending = await _resolve_pending_batch(
            client, run_file, data, target_peer, target.topic_id,
            cancel_event, status,
        )
        if pending_source_ids and recovered_pending == 0:
            await _record_unsupported_messages(
                client, source_peer, pending_source_ids, data, run_file,
            )
        emit_progress()
        await _reconcile_run(client, run_file, data, cancel_event, status)
        emit_progress()
        if status:
            status(
                "Zielabgleich abgeschlossen. Das Kopieren wird fortgesetzt…"
            )
        mapped = {int(item["source_id"]) for item in data.get("mappings") or []}
        mapped_selected = mapped & selected_set
        previous = len(mapped_selected)
        copied_now = 0
        emit_progress()
        _prepare_forward_request(client)
        batch_size = min(10, max(5, int(data.get("adaptive_batch_size") or 10)))
        no_progress_events = 0
        while True:
            mapped = {int(item["source_id"]) for item in data.get("mappings") or []}
            mapped_selected = mapped & set(selected_ids)
            uncopyable_ids = {
                int(value) for value in data.get("uncopyable_source_ids") or []
            }
            remaining_ids = [
                sid for sid in selected_ids
                if sid not in mapped_selected and sid not in uncopyable_ids
            ]
            if not remaining_ids:
                break
            if cancel_event is not None and cancel_event.is_set():
                raise TopicCopyCancelled(
                    "Kopiervorgang abgebrochen. Der bestätigte Zwischenstand "
                    "wurde gespeichert und kann später fortgesetzt werden."
                )
            batch = remaining_ids[:batch_size]
            _begin_pending_batch(data, run_file, batch)
            request = ForwardMessagesRequest(
                from_peer=source_peer, id=batch, to_peer=target_peer,
                silent=True, top_msg_id=target.topic_id,
            )
            try:
                result = await client(request)
            except errors.WorkerBusyTooLongRetryError as exc:
                wait_seconds = 8 if batch_size > 5 else 5
                data.setdefault("adaptive_events", []).append({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "event": "telegram_worker_busy",
                    "batch_size": batch_size,
                    "wait_seconds": wait_seconds,
                })
                _write_run(data, run_file)
                if status:
                    confirmed = confirmed_count()
                    status(
                        f"Telegram ist ausgelastet: {confirmed} bestätigt, "
                        f"{len(selected_ids) - confirmed} noch offen. Der "
                        "ausstehende Block wird gesichert; automatische "
                        f"Fortsetzung in {wait_seconds} Sekunden…"
                    )
                confirmed = confirmed_count()
                await _wait_for_flood_limit(
                    wait_seconds, cancel_event, status,
                    confirmed, len(selected_ids),
                )
                client._request_retries = 1
                client.flood_sleep_threshold = 60
                recovered = await _resolve_pending_batch(
                    client, run_file, data, target_peer, target.topic_id,
                    cancel_event, status,
                )
                copied_now += recovered
                if recovered:
                    no_progress_events = 0
                else:
                    no_progress_events += 1
                batch_size = _reduced_batch_size(batch_size)
                data["adaptive_batch_size"] = batch_size
                _write_run(data, run_file)
                _prepare_forward_request(client)
                if status:
                    status(
                        f"Block abgeglichen. Weiter mit {batch_size} Nachrichten "
                        "pro Telegram-Aufruf…"
                    )
                emit_progress()
                if no_progress_events >= 8:
                    raise RuntimeError(
                        "Telegram hat acht Versuche hintereinander keine einzige "
                        "Nachricht angenommen. Der bestätigte Stand ist gespeichert."
                    ) from exc
                continue
            except FloodWaitError as exc:
                data.setdefault("adaptive_events", []).append({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "event": "telegram_flood_wait",
                    "batch_size": batch_size,
                    "wait_seconds": int(exc.seconds),
                })
                _write_run(data, run_file)
                confirmed = confirmed_count()
                await _wait_for_flood_limit(
                    int(exc.seconds), cancel_event, status,
                    confirmed, len(selected_ids),
                )
                client._request_retries = 1
                client.flood_sleep_threshold = 60
                recovered = await _resolve_pending_batch(
                    client, run_file, data, target_peer, target.topic_id,
                    cancel_event, status,
                )
                copied_now += recovered
                _prepare_forward_request(client)
                if status:
                    status(
                        "Telegram-Pause beendet. Ausstehender Block wurde "
                        "abgeglichen; Kopieren wird automatisch fortgesetzt…"
                    )
                emit_progress()
                continue
            except Exception:
                # Bereits serverseitig angekommene, aber unbestätigte Kopien
                # werden beim nächsten Fortsetzen zuerst rekonstruiert.
                _write_run(data, run_file)
                raise
            forwarded = _forwarded_messages(result)
            if len(forwarded) != len(batch):
                await _interruptible_wait(10, cancel_event)
                client._request_retries = 1
                client.flood_sleep_threshold = 60
                recovered = await _resolve_pending_batch(
                    client, run_file, data, target_peer, target.topic_id,
                    cancel_event, status,
                )
                copied_now += recovered
                mapped_after_recovery = {
                    int(item["source_id"])
                    for item in data.get("mappings") or []
                }
                unresolved_batch = [
                    source_id for source_id in batch
                    if source_id not in mapped_after_recovery
                ]
                unsupported = await _record_unsupported_messages(
                    client, source_peer, unresolved_batch, data, run_file,
                )
                batch_size = _reduced_batch_size(batch_size)
                data["adaptive_batch_size"] = batch_size
                _write_run(data, run_file)
                _prepare_forward_request(client)
                emit_progress()
                if unsupported and status:
                    status(
                        f"{len(unsupported)} nicht unterstützte "
                        "Mediennachricht(en) protokolliert; normale "
                        "Nachrichten werden fortgesetzt…"
                    )
                if recovered == 0:
                    if unsupported:
                        continue
                    source_text = ", ".join(str(value) for value in batch)
                    raise RuntimeError(
                        "Telegram hat einen unvollständigen Block auch nach "
                        "dem Sicherheitsabgleich nicht bestätigt. Damit keine "
                        "Doppelkopien entstehen, wurde nicht erneut gesendet. "
                        f"Betroffene Quell-IDs: {source_text}."
                    )
                continue
            misdirected = 0
            for source_id, target_message in zip(batch, forwarded):
                mapping = {
                    "source_id": source_id, "target_id": target_message.id,
                }
                if _belongs_to_topic(target_message, target.topic_id):
                    data["mappings"].append(mapping)
                    data.setdefault("validated_target_ids", []).append(
                        target_message.id
                    )
                    mapped.add(source_id)
                    mapped_selected.add(source_id)
                    copied_now += 1
                else:
                    _archive_invalid_mapping(
                        data, mapping, target_message, "wrong_topic",
                    )
                    misdirected += 1
            _finish_pending_batch(data)
            no_progress_events = 0
            data["adaptive_batch_size"] = batch_size
            _write_run(data, run_file)
            if progress:
                progress(len(mapped_selected), len(selected_ids))
            if misdirected:
                raise RuntimeError(
                    f"Telegram hat {misdirected} Nachricht(en) nicht dem "
                    "Ziel-Topic zugeordnet. Sie zählen nicht als bestätigt; "
                    "der Kopiervorgang wurde sicher angehalten."
                )
            await _interruptible_wait(1, cancel_event)
        uncopyable_selected = {
            int(value) for value in data.get("uncopyable_source_ids") or []
        } & selected_set
        data["complete"] = len(mapped_selected | uncopyable_selected) == len(selected_ids)
        _write_run(data, run_file)
        return TopicResumeResult(
            len(selected_ids), previous, copied_now,
            len(selected_ids) - len(mapped_selected | uncopyable_selected),
            run_file, len(uncopyable_selected),
        )
    finally:
        await client.disconnect()


def replace_incomplete_run_selection(
    run_file: Path,
    source_link: str,
    target_link: str,
    messages: tuple[TopicMessageInfo, ...],
    selected_ids: set[int],
) -> None:
    """Übernimmt die aktuelle UI-Auswahl in einen unterbrochenen Lauf."""
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if data.get("complete"):
        raise ValueError("Der Lauf ist bereits vollständig.")
    if parse_topic_link(data["source_link"]) != parse_topic_link(source_link):
        raise ValueError("Die geladene Quelle gehört nicht zum unterbrochenen Lauf.")
    if parse_topic_link(data["target_link"]) != parse_topic_link(target_link):
        raise ValueError("Das geladene Ziel gehört nicht zum unterbrochenen Lauf.")
    infos = {message.message_id: message for message in messages}
    missing = selected_ids - set(infos)
    if missing:
        raise ValueError("Die aktuelle Auswahl enthält unbekannte Nachrichten-IDs.")
    previous_messages = data.get("messages") or {}
    previous_mappings = data.get("mappings") or []
    removed_confirmed = [
        item for item in previous_mappings
        if int(item["source_id"]) not in selected_ids
    ]
    data["excluded_existing_mappings"] = removed_confirmed
    # Bestätigte Zuordnungen niemals aus dem Sicherheits-Ledger entfernen. Sie
    # bleiben so später auch für eine gezielte Zielbereinigung nachweisbar.
    data["mappings"] = previous_mappings
    excluded_info = data.get("excluded_message_info") or {}
    excluded_info.update({
        key: value for key, value in previous_messages.items()
        if int(key) not in selected_ids
    })
    data["excluded_message_info"] = excluded_info
    data["messages"] = {
        str(message_id): asdict(infos[message_id])
        for message_id in sorted(selected_ids)
    }
    data["selection_updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_run(data, run_file)


async def verify_deletion_candidates(
    run_file: Path,
    location: str = "source",
) -> tuple[DeletionCandidate, ...]:
    if location not in {"source", "target"}:
        raise ValueError("Ungültiger Löschbereich.")
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if not data.get("complete"):
        raise RuntimeError("Kopiervorgang unvollständig; Löschen ist gesperrt.")
    source = parse_topic_link(data["source_link"])
    target = parse_topic_link(data["target_link"])
    client = await _authorized_client()
    try:
        source_peer = await client.get_input_entity(source.peer)
        target_peer = await client.get_input_entity(target.peer)
        await _validate_topic(client, source_peer, source.topic_id)
        await _validate_topic(client, target_peer, target.topic_id)
        mappings = data.get("mappings") or []
        duplicate_entries = (
            data.get("possible_duplicate_deliveries") or []
            if location == "target" else []
        )
        if not mappings and not duplicate_entries:
            return ()
        source_messages = (
            await client.get_messages(
                source_peer, ids=[int(m["source_id"]) for m in mappings],
            ) if mappings else []
        )
        target_messages = (
            await client.get_messages(
                target_peer, ids=[int(m["target_id"]) for m in mappings],
            ) if mappings else []
        )
        infos = dict(data.get("excluded_message_info") or {})
        infos.update(data.get("messages") or {})
        candidates = []
        for mapping, source_message, target_message in zip(mappings, source_messages, target_messages):
            if target_message is None or (location == "source" and source_message is None):
                continue
            source_id = int(mapping["source_id"])
            # Die beim Forward-Aufruf erhaltene Ziel-ID muss weiterhin im
            # richtigen Topic existieren. Die fwd_from-Ursprungs-ID ist kein
            # verlässlicher Vergleich: Beim erneuten Weiterleiten einer bereits
            # weitergeleiteten Nachricht behält Telegram die allererste
            # Ursprungs-ID statt der unmittelbar kopierten Nachrichten-ID.
            fwd = getattr(target_message, "fwd_from", None)
            reply = getattr(target_message, "reply_to", None)
            target_topic_id = (
                getattr(reply, "reply_to_top_id", None)
                or getattr(reply, "reply_to_msg_id", None)
            )
            if target_topic_id != target.topic_id:
                continue
            info = infos.get(str(source_id), {})
            target_date = getattr(target_message, "date", None)
            source_date = getattr(source_message, "date", None)
            source_fwd = getattr(source_message, "fwd_from", None)
            source_original_date = getattr(source_fwd, "date", None)
            target_original_date = getattr(fwd, "date", None)
            original_date_iso = (
                info.get("original_date_iso")
                or (
                    source_original_date.isoformat()
                    if source_original_date is not None else ""
                )
                or (
                    target_original_date.isoformat()
                    if target_original_date is not None else ""
                )
                or (
                    source_date.isoformat() if source_date is not None else ""
                )
            )
            candidates.append(DeletionCandidate(
                source_id, int(mapping["target_id"]),
                info.get("sender_name") or "Unbekannt",
                info.get("text") or "[Mediennachricht]",
                (
                    source_date.isoformat()
                    if source_date is not None
                    else info.get("date_iso") or ""
                ),
                target_date.isoformat() if target_date is not None else "",
                original_date_iso,
            ))
        if location == "target" and duplicate_entries:
            mapped_target_ids = {
                int(mapping["target_id"]) for mapping in mappings
            }
            unique_duplicates = {
                int(entry["target_id"]): entry for entry in duplicate_entries
                if int(entry["target_id"]) not in mapped_target_ids
            }
            duplicate_messages = await client.get_messages(
                target_peer, ids=list(unique_duplicates),
            )
            for target_id, target_message in zip(
                unique_duplicates, duplicate_messages,
            ):
                if target_message is None or not _belongs_to_topic(
                    target_message, target.topic_id,
                ):
                    continue
                entry = unique_duplicates[target_id]
                target_date = getattr(target_message, "date", None)
                candidates.append(DeletionCandidate(
                    0, target_id, "Mögliche Doppelkopie",
                    entry.get("text") or getattr(target_message, "message", None)
                    or "[Mediennachricht]",
                    "", target_date.isoformat() if target_date else "", "", True,
                ))
        return tuple(candidates)
    finally:
        await client.disconnect()


def _rollback_message_infos(data: dict) -> tuple[dict[str, dict], dict[int, tuple[int, str]]]:
    infos = dict(data.get("excluded_message_info") or {})
    infos.update(data.get("messages") or {})
    preview_messages = []
    for raw in infos.values():
        try:
            preview_messages.append(TopicMessageInfo(
                message_id=int(raw["message_id"]),
                sender_id=raw.get("sender_id"),
                sender_name=str(raw.get("sender_name") or "Unbekannt"),
                text=str(raw.get("text") or ""),
                is_bot=bool(raw.get("is_bot")),
                reply_to_id=raw.get("reply_to_id"),
                date_iso=str(raw.get("date_iso") or ""),
                original_date_iso=str(raw.get("original_date_iso") or ""),
                is_bot_command=bool(raw.get("is_bot_command")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    source_to_bot = {}
    for group in build_preview(tuple(preview_messages)).bot_groups:
        for source_id in group.message_ids:
            source_to_bot[int(source_id)] = (group.bot_id, group.bot_name)
    return infos, source_to_bot


def _rollback_item(
    source_id: int,
    target_id: int,
    info: dict,
    category: str,
    reason: str = "",
    target_message=None,
    bot: tuple[int, str] | None = None,
) -> RollbackAuditItem:
    target_date = getattr(target_message, "date", None)
    return RollbackAuditItem(
        source_id=source_id,
        target_id=target_id,
        sender_name=str(info.get("sender_name") or "Unbekannt"),
        text=str(info.get("text") or "[Mediennachricht]"),
        date_iso=str(info.get("original_date_iso") or info.get("date_iso") or ""),
        target_date_iso=target_date.isoformat() if target_date else "",
        category=category,
        reason=reason,
        bot_id=bot[0] if bot else None,
        bot_name=bot[1] if bot else "",
    )


async def audit_target_rollback(
    run_file: Path,
    progress: Optional[ProgressCallback] = None,
    status: Optional[StatusCallback] = None,
    cancel_event: threading.Event | None = None,
) -> RollbackAudit:
    """Prüft ausschließlich protokollierte Zielkopien für ein Rückgängigmachen."""
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if not data.get("complete"):
        raise RuntimeError("Der Kopiervorgang ist noch nicht vollständig abgeschlossen.")
    target = parse_topic_link(data["target_link"])
    infos, source_to_bot = _rollback_message_infos(data)
    mappings = list(data.get("mappings") or [])
    deleted_ids = {int(value) for value in data.get("deleted_target_ids") or []}
    rollback_state = data.get("target_rollback") or {}
    pending_delete_ids = {
        int(value) for value in rollback_state.get("pending_batch") or []
    }
    mapped_target_ids = {int(item["target_id"]) for item in mappings}
    duplicate_entries = {
        int(item["target_id"]): item
        for item in data.get("possible_duplicate_deliveries") or []
        if int(item["target_id"]) not in mapped_target_ids
    }
    ids_to_load = sorted(
        (mapped_target_ids | set(duplicate_entries)) - deleted_ids
    )
    loaded = {}
    client = await _authorized_client()
    try:
        target_peer = await client.get_input_entity(target.peer)
        await _validate_topic(client, target_peer, target.topic_id)
        total = len(ids_to_load)
        for start in range(0, total, 100):
            if cancel_event is not None and cancel_event.is_set():
                raise TopicCopyCancelled("Prüfung wurde abgebrochen.")
            batch_ids = ids_to_load[start:start + 100]
            messages = await _get_messages_with_wait(
                client, target_peer, batch_ids, cancel_event, status,
            )
            if not isinstance(messages, (list, tuple)):
                messages = [messages]
            messages = list(messages) + [None] * (len(batch_ids) - len(messages))
            for target_id, message in zip(batch_ids, messages):
                loaded[target_id] = message
            done = min(start + len(batch_ids), total)
            if progress:
                progress(done, total)
            if status:
                status(f"Zielkopien werden geprüft: {done} von {total}…")

        categories = {
            "safe": [], "ambiguous": [], "missing": [], "wrong_topic": [],
            "uncopyable": [], "already_deleted": [],
        }
        for mapping in mappings:
            source_id = int(mapping["source_id"])
            target_id = int(mapping["target_id"])
            info = infos.get(str(source_id), {})
            bot = source_to_bot.get(source_id)
            if target_id in deleted_ids:
                categories["already_deleted"].append(_rollback_item(
                    source_id, target_id, info, "already_deleted",
                    "Bereits durch den Rückgängig-Lauf gelöscht.", bot=bot,
                ))
                continue
            message = loaded.get(target_id)
            if message is None:
                if target_id in pending_delete_ids:
                    deleted_ids.add(target_id)
                    categories["already_deleted"].append(_rollback_item(
                        source_id, target_id, info, "already_deleted",
                        "Nach unterbrochenem Löschblock als entfernt bestätigt.",
                        bot=bot,
                    ))
                else:
                    categories["missing"].append(_rollback_item(
                        source_id, target_id, info, "missing",
                        "Zielnachricht existiert nicht mehr.", bot=bot,
                    ))
            elif not _belongs_to_topic(message, target.topic_id):
                categories["wrong_topic"].append(_rollback_item(
                    source_id, target_id, info, "wrong_topic",
                    "Ziel-ID gehört nicht zum erwarteten Ziel-Topic.",
                    message, bot,
                ))
            elif not info or _source_match_key(info) != _target_match_key(message):
                categories["ambiguous"].append(_rollback_item(
                    source_id, target_id, info, "ambiguous",
                    "Inhalt oder Originalzeitpunkt stimmt nicht eindeutig überein.",
                    message, bot,
                ))
            else:
                categories["safe"].append(_rollback_item(
                    source_id, target_id, info, "safe", target_message=message,
                    bot=bot,
                ))

        for target_id, entry in duplicate_entries.items():
            info = {"sender_name": "Mögliche Doppelkopie", "text": entry.get("text") or ""}
            if target_id in deleted_ids:
                categories["already_deleted"].append(_rollback_item(
                    0, target_id, info, "already_deleted",
                    "Bereits durch den Rückgängig-Lauf gelöscht.",
                ))
                continue
            message = loaded.get(target_id)
            if message is None:
                if target_id in pending_delete_ids:
                    deleted_ids.add(target_id)
                    categories["already_deleted"].append(_rollback_item(
                        0, target_id, info, "already_deleted",
                        "Nach unterbrochenem Löschblock als entfernt bestätigt.",
                    ))
                else:
                    categories["missing"].append(_rollback_item(
                        0, target_id, info, "missing",
                        "Mögliche Doppelkopie existiert nicht mehr.",
                    ))
            elif _belongs_to_topic(message, target.topic_id):
                categories["ambiguous"].append(_rollback_item(
                    0, target_id, info, "ambiguous",
                    "Als mögliche Doppelkopie erkannt; manuelle Kontrolle nötig.",
                    message,
                ))
            else:
                categories["wrong_topic"].append(_rollback_item(
                    0, target_id, info, "wrong_topic",
                    "Mögliche Doppelkopie gehört nicht zum Ziel-Topic.", message,
                ))

        for source_id in sorted({
            int(value) for value in data.get("uncopyable_source_ids") or []
        }):
            info = infos.get(str(source_id), {})
            categories["uncopyable"].append(_rollback_item(
                source_id, 0, info, "uncopyable",
                "Diese Nachricht wurde nicht kopiert und bleibt in der Quelle.",
                bot=source_to_bot.get(source_id),
            ))

        summary = {name: len(items) for name, items in categories.items()}
        data["last_target_rollback_audit"] = {
            "checked_at": datetime.now(timezone.utc).isoformat(), **summary,
        }
        data["deleted_target_ids"] = sorted(deleted_ids)
        if "target_rollback" in data:
            data["target_rollback"].pop("pending_batch", None)
        _write_run(data, run_file)
        return RollbackAudit(
            run_file=run_file,
            **{name: tuple(items) for name, items in categories.items()},
        )
    finally:
        await client.disconnect()


async def rollback_target_messages(
    run_file: Path,
    target_ids: set[int],
    progress: Optional[ProgressCallback] = None,
    status: Optional[StatusCallback] = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Löscht nur explizite, protokollierte Ziel-IDs in bestätigten Blöcken."""
    if not target_ids:
        return 0
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if not data.get("complete"):
        raise RuntimeError("Der Kopiervorgang ist noch nicht abgeschlossen.")
    allowed_ids = {
        int(item["target_id"]) for item in data.get("mappings") or []
    } | {
        int(item["target_id"])
        for item in data.get("possible_duplicate_deliveries") or []
    }
    if not target_ids <= allowed_ids:
        raise RuntimeError(
            "Die Auswahl enthält Zielnachrichten ohne Kopiernachweis. Nichts wurde gelöscht."
        )
    target = parse_topic_link(data["target_link"])
    infos, _source_to_bot = _rollback_message_infos(data)
    mapping_by_target = {
        int(item["target_id"]): item for item in data.get("mappings") or []
    }
    deleted_ids = {int(value) for value in data.get("deleted_target_ids") or []}
    remaining = sorted(target_ids - deleted_ids)
    rollback_state = data.setdefault("target_rollback", {})
    rollback_state["started_at"] = rollback_state.get("started_at") or datetime.now(
        timezone.utc,
    ).isoformat()
    rollback_state["requested_ids"] = sorted(
        set(rollback_state.get("requested_ids") or []) | target_ids
    )
    _write_run(data, run_file)
    client = await _authorized_client()
    deleted_now = 0
    try:
        target_peer = await client.get_input_entity(target.peer)
        await _validate_topic(client, target_peer, target.topic_id)
        client.flood_sleep_threshold = 10
        for start in range(0, len(remaining), 100):
            if cancel_event is not None and cancel_event.is_set():
                raise TopicCopyCancelled(
                    "Löschen abgebrochen. Bereits bestätigte Löschungen sind gespeichert."
                )
            batch = remaining[start:start + 100]
            messages = await _get_messages_with_wait(
                client, target_peer, batch, cancel_event, status,
            )
            if not isinstance(messages, (list, tuple)):
                messages = [messages]
            messages = list(messages) + [None] * (len(batch) - len(messages))
            existing_ids = []
            for target_id, message in zip(batch, messages):
                if message is None:
                    deleted_ids.add(target_id)
                    continue
                if not _belongs_to_topic(message, target.topic_id):
                    raise RuntimeError(
                        f"Ziel-ID {target_id} gehört nicht mehr zum Ziel-Topic. "
                        "Der aktuelle Block wurde nicht gelöscht."
                    )
                mapping = mapping_by_target.get(target_id)
                if mapping is not None:
                    info = infos.get(str(int(mapping["source_id"])), {})
                    if not info or _source_match_key(info) != _target_match_key(message):
                        raise RuntimeError(
                            f"Ziel-ID {target_id} stimmt nicht mehr eindeutig mit "
                            "der protokollierten Kopie überein. Der aktuelle Block "
                            "wurde nicht gelöscht."
                        )
                existing_ids.append(target_id)
            if existing_ids:
                rollback_state["pending_batch"] = list(existing_ids)
                rollback_state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _write_run(data, run_file)
                while True:
                    try:
                        await client.delete_messages(
                            target_peer, existing_ids, revoke=True,
                        )
                        break
                    except FloodWaitError as exc:
                        await _wait_for_operation_limit(
                            int(exc.seconds), cancel_event, status, "Löschen",
                        )
                    except (
                        errors.ChatAdminRequiredError,
                        errors.MessageDeleteForbiddenError,
                        errors.UserAdminInvalidError,
                        errors.ChatForbiddenError,
                    ) as exc:
                        raise RuntimeError(
                            "Telegram erlaubt das Löschen im Ziel-Topic nicht. "
                            "Dafür können Administratorrechte mit "
                            "Löschberechtigung erforderlich sein."
                        ) from exc
                check = await _get_messages_with_wait(
                    client, target_peer, existing_ids, cancel_event, status,
                )
                if not isinstance(check, (list, tuple)):
                    check = [check]
                still_present = [
                    target_id for target_id, message in zip(existing_ids, check)
                    if message is not None
                ]
                confirmed = set(existing_ids) - set(still_present)
                deleted_ids.update(confirmed)
                deleted_now += len(confirmed)
                if still_present:
                    rollback_state["failed_ids"] = sorted(
                        set(rollback_state.get("failed_ids") or []) | set(still_present)
                    )
            data["deleted_target_ids"] = sorted(deleted_ids)
            rollback_state.pop("pending_batch", None)
            rollback_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            rollback_state["deleted_count"] = len(deleted_ids)
            _write_run(data, run_file)
            done = min(start + len(batch), len(remaining))
            if progress:
                progress(done, len(remaining))
            if status:
                status(
                    f"Rückgängig: {done} von {len(remaining)} ausgewählten "
                    "Zielnachrichten geprüft und blockweise gelöscht…"
                )
            if existing_ids and still_present:
                raise RuntimeError(
                    f"{len(still_present)} Nachricht(en) des letzten Blocks sind "
                    "noch vorhanden. Der bestätigte Löschstand wurde gespeichert."
                )
        rollback_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_run(data, run_file)
        return deleted_now
    finally:
        await client.disconnect()


async def delete_verified_messages(
    run_file: Path,
    message_ids: set[int],
    location: str = "source",
) -> int:
    if location not in {"source", "target"}:
        raise ValueError("Ungültiger Löschbereich.")
    if not message_ids:
        return 0
    candidates = await verify_deletion_candidates(run_file, location)
    verified_ids = {
        candidate.source_id if location == "source" else candidate.target_id
        for candidate in candidates
    }
    if not message_ids <= verified_ids:
        raise RuntimeError(
            "Eine Auswahl ist nicht mehr sicher im Ziel nachweisbar. Nichts wurde gelöscht."
        )
    data = json.loads(run_file.read_text(encoding="utf-8"))
    topic = parse_topic_link(
        data["source_link"] if location == "source" else data["target_link"]
    )
    client = await _authorized_client()
    try:
        peer = await client.get_input_entity(topic.peer)
        # Nur explizite IDs; niemals messages.deleteTopicHistory.
        client.flood_sleep_threshold = 10
        try:
            await client.delete_messages(peer, sorted(message_ids), revoke=True)
        except (
            errors.ChatAdminRequiredError,
            errors.MessageDeleteForbiddenError,
            errors.UserAdminInvalidError,
            errors.ChatForbiddenError,
        ) as exc:
            raise RuntimeError(
                "Telegram erlaubt das Löschen dieser Nachrichten nicht. Für "
                "Nachrichten anderer Mitglieder werden in Gruppen oder Kanälen "
                "in der Regel Administratorrechte mit Löschberechtigung benötigt. "
                "Eigene Nachrichten können normalerweise ohne Adminrechte gelöscht "
                "werden. Es wurde kein kompletter Topic-Verlauf gelöscht."
            ) from exc
        ledger_key = (
            "deleted_source_ids" if location == "source" else "deleted_target_ids"
        )
        deleted = set(data.get(ledger_key) or []) | message_ids
        data[ledger_key] = sorted(deleted)
        _write_run(data, run_file)
        return len(message_ids)
    finally:
        await client.disconnect()


async def delete_verified_source_messages(run_file: Path, source_ids: set[int]) -> int:
    """Rückwärtskompatibler Wrapper für bestehende Aufrufer."""
    return await delete_verified_messages(run_file, source_ids, "source")
