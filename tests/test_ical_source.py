import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ical_source import ICalError, fetch_ical, fetch_ical_events, parse_ical
from sync_processor import CalendarSyncProcessor

SAMPLE_ICAL = """BEGIN:VCALENDAR
X-WR-TIMEZONE:Europe/Rome
PRODID:-//Politecnico di Milano//IT
BEGIN:VEVENT
UID:1172587-polimi.it
DTSTART:20260917T101500
DTEND:20260917T121500
SUMMARY:Lezione: Didattica - FORMAL LANGUAGES AND COMPILERS
LOCATION:Milano - Aula: 9.1.2
END:VEVENT
BEGIN:VEVENT
UID:1144126-polimi.it
DTSTART:20260908T084500
DTEND:20260908T124500
SUMMARY:Esame: DESIGN AND IMPLEMENTATION OF MOBILE APPLICATIONS - Baresi Luciano
DESCRIPTION:Non iscritto\\n\\nAA: 2025 - Semestre: 1° semestre
LOCATION:Milano Leonardo
END:VEVENT
BEGIN:VEVENT
UID:1148871-polimi.it
DTSTART;VALUE=DATE:20261020
DTEND;VALUE=DATE:20261021
SUMMARY:Scadenza: Esame di laurea
DESCRIPTION:LAUREA MAGISTRALE
END:VEVENT
END:VCALENDAR
"""


def test_parse_ical_lecture_event():
    events = parse_ical(SAMPLE_ICAL)
    assert len(events) == 3

    lecture = events[0]
    assert lecture["id"] == "1172587-polimi.it"
    assert lecture["summary"] == "Lezione: Didattica - FORMAL LANGUAGES AND COMPILERS"
    assert lecture["location"] == "Milano - Aula: 9.1.2"
    assert lecture["start"]["dateTime"] == "2026-09-17T10:15:00+02:00"
    assert lecture["start"]["timeZone"] == "Europe/Rome"
    assert lecture["end"]["dateTime"] == "2026-09-17T12:15:00+02:00"


def test_parse_ical_exam_description_unescaped():
    events = parse_ical(SAMPLE_ICAL)
    exam = events[1]
    assert exam["description"].startswith("Non iscritto\n\n")


def test_parse_ical_all_day_event():
    events = parse_ical(SAMPLE_ICAL)
    deadline = events[2]
    assert deadline["start"] == {"date": "2026-10-20"}
    assert deadline["end"] == {"date": "2026-10-21"}


def test_parse_ical_utc_times():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc12345
DTSTART:20260917T101500Z
DTEND:20260917T121500Z
SUMMARY:Test
END:VEVENT
END:VCALENDAR
"""
    events = parse_ical(text)
    assert events[0]["start"] == {
        "dateTime": "2026-09-17T10:15:00+00:00",
        "timeZone": "UTC",
    }


def test_parse_ical_skips_events_without_uid():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260917T101500
DTEND:20260917T121500
SUMMARY:No UID here
END:VEVENT
END:VCALENDAR
"""
    assert parse_ical(text) == []


def test_fetch_ical_events_uses_network_mock():
    with patch("ical_source.fetch_ical", return_value=SAMPLE_ICAL) as mock_fetch:
        events = fetch_ical_events("https://example.com/feed.ics")
        mock_fetch.assert_called_once_with("https://example.com/feed.ics", timeout=30)
        assert len(events) == 3


def test_sync_processor_prefers_ical_over_google_source():
    mock_client = MagicMock()
    mock_client.get_calendar_id_by_name.side_effect = lambda name: "tgt_id"
    mock_client.get_all_events.return_value = []
    mock_client.batch_mutate_events.side_effect = lambda ops: [(op, None) for op in ops]

    mock_strategy = MagicMock()
    mock_strategy.determine_color.return_value = "5"

    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=mock_strategy,
        source_name="Source",
        target_name="Target",
        source_ical_url="https://example.com/feed.ics",
    )

    with patch(
        "sync_processor.fetch_ical_events",
        return_value=[
            {
                "id": "1172587-polimi.it",
                "summary": "Lezione: Didattica - CS",
                "description": "",
                "start": {"dateTime": "2026-09-17T10:15:00+02:00"},
                "end": {"dateTime": "2026-09-17T12:15:00+02:00"},
            }
        ],
    ) as mock_fetch:
        processor.process()

    mock_fetch.assert_called_once_with("https://example.com/feed.ics")
    # Google source calendar must never be touched in iCal mode
    assert mock_client.get_all_events.call_count == 1  # target only
    called_ops = mock_client.batch_mutate_events.call_args[0][0]
    inserts = [op for op in called_ops if op["action"] == "insert"]
    assert len(inserts) == 1
    assert inserts[0]["body"]["summary"] == "CS"


def test_parse_ical_categories():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:evt12345
DTSTART:20260917T101500
DTEND:20260917T121500
CATEGORIES:Esame
SUMMARY:Esame: Something
END:VEVENT
END:VCALENDAR
"""
    events = parse_ical(text)
    assert events[0]["categories"] == ["Esame"]


def test_parse_ical_rrule_passthrough():
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:evt12345
DTSTART:20260917T101500
DTEND:20260917T121500
RRULE:FREQ=WEEKLY;COUNT=10
EXDATE:20261001T101500
SUMMARY:Lezione: Didattica - Recurring Course
END:VEVENT
END:VCALENDAR
"""
    events = parse_ical(text)
    assert events[0]["recurrence"] == [
        "RRULE:FREQ=WEEKLY;COUNT=10",
        "EXDATE:20261001T101500",
    ]


def test_parse_ical_skips_malformed_event(capsys):
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:bad12345
DTSTART:not-a-date
DTEND:20260917T121500
SUMMARY:Broken
END:VEVENT
BEGIN:VEVENT
UID:good12345
DTSTART:20260917T101500
DTEND:20260917T121500
SUMMARY:Fine
END:VEVENT
END:VCALENDAR
"""
    events = parse_ical(text)
    assert [e["id"] for e in events] == ["good12345"]
    assert "Skipping malformed" in capsys.readouterr().out


def test_fetch_ical_wraps_network_errors():
    with patch(
        "ical_source.urllib.request.urlopen",
        side_effect=urllib.error.URLError("boom"),
    ):
        with pytest.raises(ICalError, match="Could not download"):
            fetch_ical("https://example.com/feed.ics")


def test_sync_processor_exits_cleanly_on_ical_failure():
    mock_client = MagicMock()
    processor = CalendarSyncProcessor(
        client=mock_client,
        strategy=MagicMock(),
        source_name="Source",
        target_name="Target",
        source_ical_url="https://example.com/feed.ics",
    )
    with patch(
        "sync_processor.fetch_ical_events",
        side_effect=ICalError("nope"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            processor.process()
    assert exc_info.value.code == 1
    mock_client.batch_mutate_events.assert_not_called()
