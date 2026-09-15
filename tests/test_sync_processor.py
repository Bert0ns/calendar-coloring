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

    # Invalid characters stripped
    assert processor._sanitize_event_id("abc!@#def123") == "abcdef123"


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
