from unittest.mock import MagicMock

from sync_processor import CalendarSyncProcessor


def test_sanitize_event_id():
    processor = CalendarSyncProcessor(
        client=MagicMock(),
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
    )

    # Standard valid base32hex ID
    assert processor._sanitize_event_id("abcdef12345") == "abcdef12345"

    # Uppercase transformed to lowercase
    assert processor._sanitize_event_id("ABCDEF12345") == "abcdef12345"

    # Short ID (< 5 chars) is padded with polimi hash
    short_sanitized = processor._sanitize_event_id("ab1")
    assert len(short_sanitized) >= 5
    assert short_sanitized.startswith("polimi")

    # Invalid characters stripped, hash suffix appended to avoid collisions
    stripped = processor._sanitize_event_id("abc!@#def123")
    assert stripped.startswith("abcdef123")
    assert len(stripped) == len("abcdef123") + 8
    assert set(stripped) <= set("abcdefghijklmnopqrstuv0123456789")

    # Deterministic: same input always maps to the same ID
    assert processor._sanitize_event_id("abc!@#def123") == stripped


def test_sanitize_event_id_avoids_collisions():
    processor = CalendarSyncProcessor(
        client=MagicMock(),
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
    )

    # "ab-c" and "abc" would both strip to "abc" — the altered one must differ
    assert processor._sanitize_event_id("ab-c") != processor._sanitize_event_id("abc")
    # Untouched IDs keep their exact form (backward compatible)
    assert processor._sanitize_event_id("1172587polimiit") == "1172587polimiit"


def test_sync_processor_inserts_and_protects_events():
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.side_effect = lambda cid: (
        [
            {
                "id": "event12345",
                "summary": "Lezione: Didattica - CS",
                "description": "desc",
                "start": {"date": "2026-09-20"},
                "end": {"date": "2026-09-20"},
            }
        ]
        if cid == "src_id"
        else [
            # Target calendar already contains a personal unmanaged event
            {
                "id": "personal_event_999",
                "summary": "Dentist Appointment",
                "start": {"date": "2026-09-21"},
                "end": {"date": "2026-09-21"},
                # No extendedProperties
            }
        ]
    )

    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = "5"

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
        verbose=True,
    )

    processor.process()

    assert mock_client.batch_mutate_events.called
    called_ops = mock_client.batch_mutate_events.call_args[0][0]

    # Exactly 1 insert mutation for the source event
    inserts = [op for op in called_ops if op["action"] == "insert"]
    assert len(inserts) == 1
    assert inserts[0]["body"]["colorId"] == "5"
    assert inserts[0]["body"]["summary"] == "CS"
    assert inserts[0]["summary"] == "CS"
    assert (
        inserts[0]["body"]["extendedProperties"]["private"]["polimi_sync_managed"]
        == "true"
    )

    # The personal event MUST NOT be deleted because it lacks polimi_sync_managed == true
    deletes = [op for op in called_ops if op["action"] == "delete"]
    assert len(deletes) == 0


def test_sync_processor_deletes_stale_managed_event():
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.side_effect = lambda cid: (
        []
        if cid == "src_id"
        else [
            # Target has an old event that WAS managed by polimi sync
            {
                "id": "old_event_12345",
                "summary": "Old Lecture",
                "extendedProperties": {"private": {"polimi_sync_managed": "true"}},
            }
        ]
    )

    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
    )

    processor.process()

    called_ops = mock_client.batch_mutate_events.call_args[0][0]
    deletes = [op for op in called_ops if op["action"] == "delete"]
    assert len(deletes) == 1
    assert deletes[0]["event_id"] == "old_event_12345"


def test_clean_summary():
    processor = CalendarSyncProcessor(
        client=MagicMock(),
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
    )

    # Lecture prefix is stripped
    assert (
        processor._clean_summary("Lezione: Didattica - Computer Security")
        == "Computer Security"
    )
    # Exam summary is unmodified
    assert (
        processor._clean_summary("Esame: Computer Security - Appello 1")
        == "Esame: Computer Security - Appello 1"
    )
    # Generic event is unmodified
    assert processor._clean_summary("Meeting") == "Meeting"


def test_sync_processor_updates_stale_summary_with_prefix():
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.side_effect = lambda cid: (
        [
            {
                "id": "event12345",
                "summary": "Lezione: Didattica - Machine Learning",
                "description": "desc",
                "start": {"date": "2026-09-20"},
                "end": {"date": "2026-09-20"},
            }
        ]
        if cid == "src_id"
        else [
            {
                "id": "event12345",
                "summary": "Lezione: Didattica - Machine Learning",
                "description": "desc",
                "colorId": "3",
                "start": {"date": "2026-09-20"},
                "end": {"date": "2026-09-20"},
                "extendedProperties": {"private": {"polimi_sync_managed": "true"}},
            }
        ]
    )
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = "3"

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
    )

    processor.process()

    assert mock_client.batch_mutate_events.called
    called_ops = mock_client.batch_mutate_events.call_args[0][0]

    updates = [op for op in called_ops if op["action"] == "update"]
    assert len(updates) == 1
    assert updates[0]["event_id"] == "event12345"
    assert updates[0]["body"]["summary"] == "Machine Learning"
    assert updates[0]["summary"] == "Machine Learning"


def _managed_event(event_id, summary="Old", start="2020-01-10"):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"date": start},
        "end": {"date": start},
        "extendedProperties": {"private": {"polimi_sync_managed": "true"}},
    }


def test_dry_run_computes_but_applies_nothing(capsys):
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.side_effect = lambda cid: (
        [
            {
                "id": "event12345",
                "summary": "Lezione: Didattica - CS",
                "description": "desc",
                "start": {"date": "2026-09-20"},
                "end": {"date": "2026-09-20"},
            }
        ]
        if cid == "src_id"
        else []
    )

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = "5"

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
        dry_run=True,
    )
    processor.process()

    mock_client.batch_mutate_events.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out


def test_quiet_suppresses_info_output(capsys):
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.return_value = []
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
        quiet=True,
        verbose=True,
    )
    processor.process()

    assert capsys.readouterr().out == ""


def test_prune_before_deletes_only_old_managed_events():
    from datetime import date

    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    old_managed = _managed_event("old12345", start="2020-01-10")
    new_managed = _managed_event("recent12345", start="2026-01-10")
    unmanaged = {
        "id": "personal99999",
        "summary": "Dentist",
        "start": {"date": "2020-01-10"},
        "end": {"date": "2020-01-10"},
    }
    source_event = {
        "id": "recent12345",
        "summary": "Old",
        "description": "",
        "start": {"date": "2026-01-10"},
        "end": {"date": "2026-01-10"},
    }
    mock_client.get_all_events.side_effect = lambda cid: (
        [source_event] if cid == "src_id" else [new_managed, unmanaged]
    )
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = None

    # Case 1: old event absent from source -> single stale delete, no duplicate
    mock_client.get_all_events.side_effect = lambda cid: (
        [source_event] if cid == "src_id" else [old_managed, new_managed, unmanaged]
    )
    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
        prune_before=date(2025, 1, 1),
    )
    processor.process()

    called_ops = mock_client.batch_mutate_events.call_args[0][0]
    deletes = [op for op in called_ops if op["action"] == "delete"]
    assert [op["event_id"] for op in deletes] == ["old12345"]

    # Case 2: old event still in source -> pruned anyway, unmanaged untouched
    mock_client.get_all_events.side_effect = lambda cid: (
        [
            source_event,
            {
                "id": "old12345",
                "summary": "Old",
                "description": "",
                "start": {"date": "2020-01-10"},
                "end": {"date": "2020-01-10"},
            },
        ]
        if cid == "src_id"
        else [old_managed, new_managed, unmanaged]
    )
    mock_client.batch_mutate_events.reset_mock()
    processor.process()

    called_ops = mock_client.batch_mutate_events.call_args[0][0]
    deletes = [op for op in called_ops if op["action"] == "delete"]
    assert [op["event_id"] for op in deletes] == ["old12345"]


def test_recurrence_change_triggers_update():
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    source_event = {
        "id": "event12345",
        "summary": "Lezione: Didattica - CS",
        "description": "",
        "start": {"dateTime": "2026-09-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-09-20T12:00:00+02:00"},
        "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=10"],
    }
    target_event = {
        "id": "event12345",
        "summary": "CS",
        "description": "",
        "start": {"dateTime": "2026-09-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-09-20T12:00:00+02:00"},
        "extendedProperties": {"private": {"polimi_sync_managed": "true"}},
    }
    mock_client.get_all_events.side_effect = lambda cid: (
        [source_event] if cid == "src_id" else [target_event]
    )
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = None

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
    )
    processor.process()

    called_ops = mock_client.batch_mutate_events.call_args[0][0]
    updates = [op for op in called_ops if op["action"] == "update"]
    assert len(updates) == 1
    assert updates[0]["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=10"]


def test_event_without_start_is_skipped(capsys):
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: (
        "src_id" if name == "Source" else "tgt_id"
    )
    mock_client.get_all_events.side_effect = lambda cid: (
        [{"id": "nostart12345", "summary": "Broken"}] if cid == "src_id" else []
    )
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
    )
    processor.process()

    mock_client.batch_mutate_events.assert_not_called()
    assert "no start time" in capsys.readouterr().out
