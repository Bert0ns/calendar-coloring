import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from unical.ical_source import (
    ICalError,
    IcalFeedSource,
    fetch_ical,
    parse_ical,
)
from unical.sync.source import SourceError

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


def wrap(*lines: str) -> str:
    return "\n".join(
        ["BEGIN:VCALENDAR", "BEGIN:VEVENT", *lines, "END:VEVENT", "END:VCALENDAR"]
    )


def test_parse_ical_lecture_event() -> None:
    events = parse_ical(SAMPLE_ICAL)
    assert len(events) == 3

    lecture = events[0]
    assert lecture["id"] == "1172587-polimi.it"
    assert lecture["summary"] == "Lezione: Didattica - FORMAL LANGUAGES AND COMPILERS"
    assert lecture["location"] == "Milano - Aula: 9.1.2"
    assert lecture["start"] == {
        "dateTime": "2026-09-17T10:15:00+02:00",
        "timeZone": "Europe/Rome",
    }
    assert lecture["end"]["dateTime"] == "2026-09-17T12:15:00+02:00"
    assert "description" not in lecture


def test_parse_ical_exam_description_unescaped() -> None:
    exam = parse_ical(SAMPLE_ICAL)[1]
    assert exam["description"].startswith("Non iscritto\n\n")


def test_parse_ical_all_day_event() -> None:
    deadline = parse_ical(SAMPLE_ICAL)[2]
    assert deadline["start"] == {"date": "2026-10-20"}
    assert deadline["end"] == {"date": "2026-10-21"}


def test_parse_ical_all_day_without_value_param() -> None:
    [event] = parse_ical(wrap("UID:abc12345", "DTSTART:20261020"))
    assert event["start"] == {"date": "2026-10-20"}


def test_parse_ical_utc_times() -> None:
    [event] = parse_ical(
        wrap("UID:abc12345", "DTSTART:20260917T101500Z", "DTEND:20260917T121500Z")
    )
    assert event["start"] == {
        "dateTime": "2026-09-17T10:15:00+00:00",
        "timeZone": "UTC",
    }


def test_parse_ical_explicit_and_unknown_tzid() -> None:
    [known] = parse_ical(
        wrap("UID:abc12345", "DTSTART;TZID=Europe/London:20260117T101500")
    )
    [unknown] = parse_ical(
        wrap("UID:abc12345", "DTSTART;TZID=Mars/Olympus:20260117T101500")
    )
    assert known["start"] == {
        "dateTime": "2026-01-17T10:15:00+00:00",
        "timeZone": "Europe/London",
    }
    assert unknown["start"] == {
        "dateTime": "2026-01-17T10:15:00+01:00",
        "timeZone": "Europe/Rome",
    }


def test_parse_ical_calendar_timezone_overrides_default() -> None:
    text = wrap("UID:abc12345", "DTSTART:20260117T101500").replace(
        "BEGIN:VCALENDAR", "BEGIN:VCALENDAR\nX-WR-TIMEZONE:America/New_York", 1
    )
    [event] = parse_ical(text)
    assert event["start"]["timeZone"] == "America/New_York"


def test_parse_ical_unfolds_continuation_lines() -> None:
    [event] = parse_ical(
        wrap("UID:abc12345", "SUMMARY:Lezione: Didattica - VERY", "  LONG NAME")
    )
    assert event["summary"] == "Lezione: Didattica - VERY LONG NAME"


def test_parse_ical_unescapes_text() -> None:
    [event] = parse_ical(wrap("UID:abc12345", r"SUMMARY:a\, b\; c\\d"))
    assert event["summary"] == r"a, b; c\d"


def test_parse_ical_skips_events_without_uid() -> None:
    assert parse_ical(wrap("DTSTART:20260917T101500", "SUMMARY:No UID here")) == []
    assert parse_ical(wrap("UID:   ", "SUMMARY:Blank UID")) == []


def test_parse_ical_keeps_first_property_occurrence() -> None:
    [event] = parse_ical(wrap("UID:abc12345", "SUMMARY:First", "SUMMARY:Second"))
    assert event["summary"] == "First"


def test_parse_ical_ignores_lines_outside_events_and_without_colon() -> None:
    text = "SUMMARY:outside\n" + wrap("UID:abc12345", "GARBAGE LINE", "SUMMARY:In")
    [event] = parse_ical(text)
    assert event["summary"] == "In"


def test_parse_ical_categories() -> None:
    [event] = parse_ical(wrap("UID:evt12345", "CATEGORIES:Esame, Altro ,", "SUMMARY:X"))
    assert event["categories"] == ["Esame", "Altro"]


def test_parse_ical_rrule_passthrough() -> None:
    [event] = parse_ical(
        wrap(
            "UID:evt12345",
            "DTSTART:20260917T101500",
            "RRULE:FREQ=WEEKLY;COUNT=10",
            "EXDATE:20261001T101500",
        )
    )
    assert event["recurrence"] == [
        "RRULE:FREQ=WEEKLY;COUNT=10",
        "EXDATE:20261001T101500",
    ]


def test_parse_ical_recurrence_does_not_leak_between_events() -> None:
    text = SAMPLE_ICAL.replace(
        "SUMMARY:Lezione: Didattica - FORMAL",
        "RRULE:FREQ=WEEKLY\nSUMMARY:Lezione: Didattica - FORMAL",
    )
    events = parse_ical(text)
    assert "recurrence" in events[0]
    assert all("recurrence" not in e for e in events[1:])


def test_parse_ical_skips_malformed_event_with_warning() -> None:
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:bad12345
DTSTART:not-a-date
SUMMARY:Broken
END:VEVENT
BEGIN:VEVENT
UID:good12345
DTSTART:20260917T101500
SUMMARY:Fine
END:VEVENT
END:VCALENDAR
"""
    warnings: list[str] = []
    events = parse_ical(text, on_warning=warnings.append)
    assert [e["id"] for e in events] == ["good12345"]
    assert warnings == [
        "Skipping malformed event (UID 'bad12345'): bad date format.",
        "Skipped 1 malformed event(s) from the iCal feed.",
    ]


def test_fetch_ical_downloads_and_decodes() -> None:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = "Caffè".encode()
    with patch(
        "unical.ical_source.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        assert fetch_ical("https://example.com/feed.ics", timeout=5) == "Caffè"
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.com/feed.ics"
    assert urlopen.call_args.kwargs == {"timeout": 5}


def test_fetch_ical_rewrites_webcal_to_https() -> None:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"BEGIN:VCALENDAR"
    with patch(
        "unical.ical_source.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        assert (
            fetch_ical("webcal://example.com/feed.ics?token=xyz") == "BEGIN:VCALENDAR"
        )
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.com/feed.ics?token=xyz"

    with patch(
        "unical.ical_source.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        assert fetch_ical("WEBCAL://example.com/feed.ics") == "BEGIN:VCALENDAR"
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.com/feed.ics"


@pytest.mark.parametrize(
    "invalid_url",
    [
        "file:///etc/shadow",
        "file:///C:/secrets.txt",
        "ftp://example.com/calendar.ics",
        "gopher://example.com/feed.ics",
        "",
        "not_a_url",
        "javascript:alert(1)",
    ],
)
def test_fetch_ical_rejects_unsupported_or_unsafe_schemes(invalid_url: str) -> None:
    with pytest.raises(ICalError, match="Unsupported or unsafe URL scheme") as exc_info:
        fetch_ical(invalid_url)
    assert isinstance(exc_info.value, SourceError)


@pytest.mark.parametrize(
    "error", [urllib.error.URLError("boom"), TimeoutError(), ValueError("bad url")]
)
def test_fetch_ical_wraps_network_errors(error: Exception) -> None:
    with (
        patch(
            "unical.ical_source.urllib.request.urlopen",
            side_effect=error,
        ),
        pytest.raises(ICalError, match="Could not download") as exc_info,
    ):
        fetch_ical("https://example.com/feed.ics")
    assert isinstance(exc_info.value, SourceError)


def test_ical_feed_source() -> None:
    warnings: list[str] = []
    source = IcalFeedSource(
        "https://example.com/secret-token",
        on_warning=warnings.append,
        fetch=lambda url: SAMPLE_ICAL.replace("DTSTART:20260908T084500", "DTSTART:x"),
    )
    assert source.label == "iCal feed"
    assert "secret" not in source.label
    assert [e["id"] for e in source.fetch_events()] == [
        "1172587-polimi.it",
        "1148871-polimi.it",
    ]
    assert len(warnings) == 2

from datetime import date


def test_parse_ical_filters_by_time_min_and_time_max() -> None:
    # Event 1: 2026-09-17, Event 2: 2026-09-08, Event 3: 2026-10-20
    all_events = parse_ical(SAMPLE_ICAL)
    assert len(all_events) == 3

    filtered = parse_ical(SAMPLE_ICAL, time_min=date(2026, 9, 15), time_max=date(2026, 9, 30))
    assert [e["id"] for e in filtered] == ["1172587-polimi.it"]

    only_after = parse_ical(SAMPLE_ICAL, time_min=date(2026, 9, 15))
    assert [e["id"] for e in only_after] == ["1172587-polimi.it", "1148871-polimi.it"]

    only_before = parse_ical(SAMPLE_ICAL, time_max=date(2026, 9, 15))
    assert [e["id"] for e in only_before] == ["1144126-polimi.it"]
