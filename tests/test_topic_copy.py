import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
import pipeline.topic_copy as topic_copy_module

from pipeline.topic_copy import (
    TopicMessageInfo,
    TopicReference,
    _belongs_to_topic,
    _begin_pending_batch,
    _iter_messages_after_id,
    _get_messages_with_wait,
    _prepare_forward_request,
    _reduced_batch_size,
    _record_unsupported_messages,
    _reconcile_run,
    _revalidate_recorded_mappings,
    _resolve_pending_batch,
    _wait_for_flood_limit,
    audit_target_rollback,
    build_preview,
    parse_topic_link,
    replace_incomplete_run_selection,
    rollback_target_messages,
)


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://t.me/c/123456/42", TopicReference(-100123456, 42)),
        ("https://t.me/c/123456/42/99", TopicReference(-100123456, 42)),
        ("t.me/meinegruppe/42", TopicReference("meinegruppe", 42)),
        ("https://t.me/s/meinegruppe/42", TopicReference("meinegruppe", 42)),
    ],
)
def test_parse_topic_link(link, expected):
    assert parse_topic_link(link) == expected


@pytest.mark.parametrize("link", ["", "https://example.com/x/2", "https://t.me/c/nope/2"])
def test_parse_topic_link_rejects_invalid_values(link):
    with pytest.raises(ValueError):
        parse_topic_link(link)


def test_build_preview_groups_command_and_bot_answer():
    messages = (
        TopicMessageInfo(10, 1, "Anna", "/status", False, None, is_bot_command=True),
        TopicMessageInfo(11, 99, "Status Bot (@status_bot)", "Fertig", True, 10),
        TopicMessageInfo(12, 2, "Ben", "Normal", False, None),
    )

    preview = build_preview(messages)

    assert len(preview.bot_groups) == 1
    assert preview.bot_groups[0].request_ids == (10,)
    assert preview.bot_groups[0].response_ids == (11,)
    assert preview.unassigned_command_ids == ()


def test_build_preview_keeps_unassigned_command_separate():
    messages = (
        TopicMessageInfo(10, 1, "Anna", "/status", False, None, is_bot_command=True),
        TopicMessageInfo(11, 99, "Status Bot (@status_bot)", "Fertig", True, None),
        TopicMessageInfo(12, 98, "Other Bot (@other_bot)", "Bereit", True, None),
    )

    preview = build_preview(messages)

    assert preview.bot_groups[0].request_ids == ()
    assert preview.unassigned_command_ids == (10,)


def test_build_preview_assigns_explicit_bot_username():
    messages = (
        TopicMessageInfo(
            10, 1, "Anna", "/status@status_bot", False, None,
            is_bot_command=True,
        ),
        TopicMessageInfo(11, 99, "Status Bot (@status_bot)", "Fertig", True, None),
    )

    preview = build_preview(messages)

    assert preview.bot_groups[0].request_ids == (10,)


def test_build_preview_learns_plain_command_from_safe_assignment():
    messages = (
        TopicMessageInfo(
            10, 1, "Anna", "/training@training_bot", False, None,
            is_bot_command=True,
        ),
        TopicMessageInfo(
            11, 2, "Ben", "/training", False, None,
            is_bot_command=True,
        ),
        TopicMessageInfo(12, 99, "Training Bot (@training_bot)", "OK", True, 10),
        TopicMessageInfo(13, 98, "Other Bot (@other_bot)", "Bereit", True, None),
    )

    preview = build_preview(messages)
    training_group = next(group for group in preview.bot_groups if group.bot_id == 99)

    assert training_group.request_ids == (10, 11)
    assert preview.unassigned_command_ids == ()


def test_build_preview_does_not_treat_path_as_bot_command():
    messages = (
        TopicMessageInfo(10, 1, "Anna", "/home/michael/datei", False, None),
        TopicMessageInfo(11, 99, "Status Bot (@status_bot)", "Fertig", True, None),
    )

    preview = build_preview(messages)

    assert preview.bot_groups[0].request_ids == ()
    assert preview.unassigned_command_ids == ()


def test_message_must_belong_to_expected_target_topic():
    correct = SimpleNamespace(
        id=100, reply_to=SimpleNamespace(reply_to_top_id=42, reply_to_msg_id=50),
    )
    wrong = SimpleNamespace(
        id=101, reply_to=SimpleNamespace(reply_to_top_id=7, reply_to_msg_id=8),
    )

    assert _belongs_to_topic(correct, 42)
    assert not _belongs_to_topic(wrong, 42)


def test_revalidation_removes_missing_and_wrong_topic_mappings(tmp_path):
    run_file = tmp_path / "run.json"
    data = {
        "mappings": [
            {"source_id": 1, "target_id": 101},
            {"source_id": 2, "target_id": 102},
            {"source_id": 3, "target_id": 103},
        ],
    }

    class FakeClient:
        async def get_messages(self, _peer, ids):
            assert ids == [101, 102, 103]
            return [
                SimpleNamespace(
                    id=101,
                    reply_to=SimpleNamespace(
                        reply_to_top_id=42, reply_to_msg_id=50,
                    ),
                ),
                SimpleNamespace(
                    id=102,
                    reply_to=SimpleNamespace(
                        reply_to_top_id=7, reply_to_msg_id=8,
                    ),
                ),
                None,
            ]

    statuses = []
    invalid = asyncio.run(_revalidate_recorded_mappings(
        FakeClient(), run_file, data, object(), 42, status=statuses.append,
    ))

    assert invalid == 2
    assert data["mappings"] == [{"source_id": 1, "target_id": 101}]
    assert {item["reason"] for item in data["invalidated_mappings"]} == {
        "wrong_topic", "missing_target_message",
    }
    assert "0 von 3" in statuses[0]


def test_pending_batch_recovers_server_deliveries_before_resending(tmp_path):
    run_file = tmp_path / "run.json"
    data = {
        "messages": {
            "1": {"text": "A"},
            "2": {"text": "B"},
        },
        "mappings": [],
    }
    _begin_pending_batch(data, run_file, [1, 2])
    # Der Test simuliert einen späteren erneuten Programmstart.
    data["pending_batch"]["started_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()

    class FakeClient:
        async def get_me(self):
            return SimpleNamespace(id=99)

        async def get_messages(self, _peer, ids=None, limit=None):
            if limit == 1:
                return [SimpleNamespace(id=502)]
            result = []
            for message_id in ids:
                text = {501: "A", 502: "B"}.get(message_id)
                result.append(
                    SimpleNamespace(
                        id=message_id, message=text, sender_id=99,
                        date=datetime.now(timezone.utc),
                        reply_to=SimpleNamespace(
                            reply_to_top_id=42, reply_to_msg_id=42,
                        ),
                    ) if text is not None else None
                )
            return result

    statuses = []
    recovered = asyncio.run(_resolve_pending_batch(
        FakeClient(), run_file, data, object(), 42, status=statuses.append,
    ))

    assert recovered == 2
    assert "pending_batch" not in data
    assert data["mappings"] == [
        {"source_id": 1, "target_id": 501},
        {"source_id": 2, "target_id": 502},
    ]
    assert "1 Zielnachrichten geprüft" in statuses[0]


def test_forward_requests_disable_automatic_retries():
    client = SimpleNamespace(_request_retries=1, flood_sleep_threshold=60)

    _prepare_forward_request(client)

    assert client._request_retries == 0
    assert client.flood_sleep_threshold == 10


def test_incomplete_forward_reduces_batch_to_five():
    assert _reduced_batch_size(10) == 5
    assert _reduced_batch_size(5) == 5


def test_unsupported_media_is_recorded_and_never_treated_as_copied(tmp_path):
    run_file = tmp_path / "run.json"
    data = {}

    class FakeClient:
        async def get_messages(self, _peer, ids):
            return [
                SimpleNamespace(
                    id=ids[0], media=__import__(
                        "telethon.tl.types", fromlist=["MessageMediaUnsupported"],
                    ).MessageMediaUnsupported(),
                ),
                SimpleNamespace(id=ids[1], media=None),
            ]

    unsupported = asyncio.run(_record_unsupported_messages(
        FakeClient(), object(), [10, 11], data, run_file,
    ))

    assert unsupported == {10}
    assert data["uncopyable_source_ids"] == [10]
    assert data["uncopyable_messages"][0]["reason"] == "message_media_unsupported"


def test_reconcile_uses_saved_scan_position(tmp_path):
    run_file = tmp_path / "run.json"
    data = {
        "target_link": "https://t.me/c/123/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "messages": {},
        "mappings": [],
        "last_reconcile_target_max_id": 456,
    }

    class FakeClient:
        _request_retries = 0
        flood_sleep_threshold = 10

        async def get_input_entity(self, peer):
            return peer

        async def get_messages(self, _peer, ids=None, limit=None):
            if ids == 1:
                return SimpleNamespace(action=None)
            assert limit == 1
            return [SimpleNamespace(id=456)]

        async def get_me(self):
            return SimpleNamespace(id=99)

        async def iter_messages(self, *_args, **kwargs):
            assert kwargs["min_id"] == 456
            if False:
                yield None

    added = asyncio.run(_reconcile_run(FakeClient(), run_file, data))

    assert added == 0
    assert data["last_reconcile_target_max_id"] == 456


def test_flood_wait_status_shows_confirmed_and_remaining(monkeypatch):
    statuses = []

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("pipeline.topic_copy.asyncio.sleep", no_wait)
    asyncio.run(_wait_for_flood_limit(
        1, None, statuses.append, confirmed=10146, total=15506,
    ))

    assert "10146 bestätigt" in statuses[0]
    assert "5360 noch offen" in statuses[0]


def test_message_check_waits_and_retries_after_flood_wait(monkeypatch):
    statuses = []

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("pipeline.topic_copy.asyncio.sleep", no_wait)

    class FakeClient:
        calls = 0

        async def get_messages(self, _peer, ids):
            self.calls += 1
            if self.calls == 1:
                raise topic_copy_module.FloodWaitError(None, capture=1)
            return [SimpleNamespace(id=value) for value in ids]

    client = FakeClient()
    result = asyncio.run(_get_messages_with_wait(
        client, object(), [10], None, statuses.append,
    ))

    assert client.calls == 2
    assert result[0].id == 10
    assert "Telegram-Pause beim Prüfen" in statuses[0]


def test_new_target_messages_are_loaded_by_direct_ids():
    class FakeClient:
        async def get_messages(self, _peer, ids=None, limit=None):
            if limit == 1:
                return [SimpleNamespace(id=503)]
            assert ids == [501, 502, 503]
            return [SimpleNamespace(id=501), None, SimpleNamespace(id=503)]

    async def collect_ids():
        return [
            message.id
            async for message in _iter_messages_after_id(
                FakeClient(), object(), 500,
            )
        ]

    assert asyncio.run(collect_ids()) == [501, 503]


def test_rollback_audit_separates_safe_and_problem_cases(tmp_path, monkeypatch):
    stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    run_file = tmp_path / "run.json"
    infos = {
        str(source_id): {
            "message_id": source_id, "sender_id": source_id,
            "sender_name": f"User {source_id}", "text": text,
            "is_bot": False, "reply_to_id": None,
            "date_iso": stamp.isoformat(), "original_date_iso": "",
            "is_bot_command": False,
        }
        for source_id, text in [
            (1, "A"), (2, "B"), (3, "C"), (4, "D"), (6, "F"), (8, "H"),
        ]
    }
    run_file.write_text(json.dumps({
        "complete": True,
        "target_link": "https://t.me/c/123/42",
        "messages": infos,
        "mappings": [
            {"source_id": 1, "target_id": 101},
            {"source_id": 2, "target_id": 102},
            {"source_id": 3, "target_id": 103},
            {"source_id": 4, "target_id": 104},
            {"source_id": 6, "target_id": 106},
            {"source_id": 8, "target_id": 108},
        ],
        "possible_duplicate_deliveries": [{"target_id": 105, "text": "A"}],
        "uncopyable_source_ids": [7],
        "excluded_message_info": {"7": {
            "message_id": 7, "sender_name": "User 7", "text": "",
            "is_bot": False, "reply_to_id": None,
        }},
        "deleted_target_ids": [106],
        "target_rollback": {"pending_batch": [102]},
    }), encoding="utf-8")

    def message(message_id, text, topic=42):
        return SimpleNamespace(
            id=message_id, message=text, date=stamp, sender_id=99,
            fwd_from=SimpleNamespace(date=stamp),
            reply_to=SimpleNamespace(reply_to_top_id=topic, reply_to_msg_id=topic),
        )

    available = {
        101: message(101, "A"),
        102: None,
        103: message(103, "C", topic=7),
        104: message(104, "geändert"),
        105: message(105, "A"),
    }

    class FakeClient:
        async def get_input_entity(self, peer):
            return peer

        async def get_messages(self, _peer, ids):
            return [available.get(value) for value in ids]

        async def disconnect(self):
            return None

    async def authorized():
        return FakeClient()

    async def validate(*_args):
        return "Topic"

    monkeypatch.setattr(topic_copy_module, "_authorized_client", authorized)
    monkeypatch.setattr(topic_copy_module, "_validate_topic", validate)

    audit = asyncio.run(audit_target_rollback(run_file))

    assert [item.target_id for item in audit.safe] == [101]
    assert {item.target_id for item in audit.ambiguous} == {104, 105}
    assert [item.target_id for item in audit.missing] == [108]
    assert [item.target_id for item in audit.wrong_topic] == [103]
    assert [item.source_id for item in audit.uncopyable] == [7]
    assert {item.target_id for item in audit.already_deleted} == {102, 106}
    saved = json.loads(run_file.read_text(encoding="utf-8"))
    assert saved["deleted_target_ids"] == [102, 106]
    assert "pending_batch" not in saved["target_rollback"]


def test_target_rollback_persists_only_confirmed_deletions(tmp_path, monkeypatch):
    stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({
        "complete": True,
        "target_link": "https://t.me/c/123/42",
        "messages": {"1": {
            "message_id": 1, "sender_name": "User", "text": "A",
            "date_iso": stamp.isoformat(), "original_date_iso": "",
        }},
        "mappings": [{"source_id": 1, "target_id": 101}],
    }), encoding="utf-8")
    target_message = SimpleNamespace(
        id=101, message="A", date=stamp,
        fwd_from=SimpleNamespace(date=stamp),
        reply_to=SimpleNamespace(reply_to_top_id=42, reply_to_msg_id=42),
    )

    class FakeClient:
        deleted = False

        async def get_input_entity(self, peer):
            return peer

        async def get_messages(self, _peer, ids):
            return [None if self.deleted else target_message for _value in ids]

        async def delete_messages(self, _peer, ids, revoke):
            assert ids == [101]
            assert revoke is True
            self.deleted = True

        async def disconnect(self):
            return None

    client = FakeClient()

    async def authorized():
        return client

    async def validate(*_args):
        return "Topic"

    monkeypatch.setattr(topic_copy_module, "_authorized_client", authorized)
    monkeypatch.setattr(topic_copy_module, "_validate_topic", validate)

    deleted = asyncio.run(rollback_target_messages(run_file, {101}))
    saved = json.loads(run_file.read_text(encoding="utf-8"))

    assert deleted == 1
    assert saved["deleted_target_ids"] == [101]


def test_replacing_resume_selection_preserves_confirmed_ledger(tmp_path):
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({
        "complete": False,
        "source_link": "https://t.me/c/123/1",
        "target_link": "https://t.me/c/123/2",
        "messages": {
            "10": {"message_id": 10, "sender_name": "Bot", "text": "alt"},
            "11": {"message_id": 11, "sender_name": "Anna", "text": "neu"},
        },
        "mappings": [
            {"source_id": 10, "target_id": 110},
            {"source_id": 11, "target_id": 111},
        ],
    }), encoding="utf-8")
    messages = (
        TopicMessageInfo(10, 1, "Bot", "alt", True, None),
        TopicMessageInfo(11, 2, "Anna", "neu", False, None),
    )

    replace_incomplete_run_selection(
        run_file, "https://t.me/c/123/1", "https://t.me/c/123/2",
        messages, {11},
    )

    data = json.loads(run_file.read_text(encoding="utf-8"))
    assert data["messages"].keys() == {"11"}
    assert {item["source_id"] for item in data["mappings"]} == {10, 11}
    assert data["excluded_message_info"]["10"]["text"] == "alt"
