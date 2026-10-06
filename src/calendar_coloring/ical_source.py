"""Fetches and parses iCal feeds into Google Calendar event dicts.

Supports the university iCal format (flat VEVENTs, floating Europe/Rome times)
using only the standard library.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_coloring.events import Event
from calendar_coloring.sync.source import SourceError

DEFAULT_TIMEZONE = "Europe/Rome"
DEFAULT_TIMEOUT = 30
RECURRENCE_PROPERTIES = ("RRULE", "EXDATE", "RDATE")

WarningSink = Callable[[str], None]


def _ignore_warning(_: str) -> None:
    pass


class ICalError(SourceError):
    """Raised when an iCal feed cannot be downloaded or is unusable."""


def mask_url(url: str) -> str:
    """Hides credentials/tokens in a URL so it is safe to print in logs."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted>"
    if not parts.netloc:
        return "<redacted>"
    return f"{parts.scheme}://{parts.netloc}/<redacted>"


def fetch_ical(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Downloads raw iCal text from a URL."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ICalError(
            "Could not download iCal feed. Check the URL (it may have expired "
            f"or been revoked) and your network connection: {exc}"
        ) from exc
    return body.decode("utf-8", errors="replace")


def _unfold_lines(text: str) -> list[str]:
    """Joins folded iCal lines (continuation lines start with space/tab)."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    """Unescapes iCal text values (\\n, \\,, \\;, \\\\)."""
    return (
        value.replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
    )


def _parse_property(line: str) -> tuple[str, dict[str, str], str] | None:
    """Splits an iCal content line into (name, params, value)."""
    if ":" not in line:
        return None
    left, _, value = line.partition(":")
    parts = left.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, _, val = part.partition("=")
            params[key.upper()] = val.strip('"')
    return parts[0].upper(), params, value


def _parse_datetime(value: str, params: dict[str, str], default_tz: str) -> Event:
    """Converts a DTSTART/DTEND value into a Google Calendar time dict:
    ``{"date": ...}`` or ``{"dateTime": ..., "timeZone": ...}``."""
    value_type = params.get("VALUE", "").upper()
    tzid = params.get("TZID", default_tz)

    if value_type == "DATE" or (len(value) == 8 and "T" not in value):
        return {
            "date": date(int(value[0:4]), int(value[4:6]), int(value[6:8])).isoformat()
        }

    # DATE-TIME: YYYYMMDDTHHMMSS with optional trailing Z (UTC)
    is_utc = value.endswith("Z")
    moment = datetime.strptime(value[:-1] if is_utc else value, "%Y%m%dT%H%M%S")
    if is_utc:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
        tzid = "UTC"
    else:
        try:
            moment = moment.replace(tzinfo=ZoneInfo(tzid))
        except (ZoneInfoNotFoundError, ValueError):
            moment = moment.replace(tzinfo=ZoneInfo(default_tz))
            tzid = default_tz
    return {"dateTime": moment.isoformat(), "timeZone": tzid}


def _calendar_timezone(lines: list[str], fallback: str) -> str:
    """The calendar-level X-WR-TIMEZONE, if any."""
    for line in lines:
        if line.upper().startswith("X-WR-TIMEZONE"):
            parsed = _parse_property(line)
            if parsed and parsed[2].strip():
                return parsed[2].strip()
            break
    return fallback


def _build_event(
    props: dict[str, tuple[dict[str, str], str]],
    recurrence: list[str],
    default_tz: str,
) -> Event | None:
    """Builds an event from VEVENT properties. Raises ValueError on bad dates."""
    uid = props["UID"][1].strip() if "UID" in props else ""
    if not uid:
        return None

    def text_of(name: str) -> str:
        return _unescape(props[name][1]) if name in props else ""

    event: Event = {"id": uid, "summary": text_of("SUMMARY")}
    for name, key in (("DESCRIPTION", "description"), ("LOCATION", "location")):
        if text := text_of(name):
            event[key] = text
    for name, key in (("DTSTART", "start"), ("DTEND", "end")):
        if name in props:
            params, value = props[name]
            event[key] = _parse_datetime(value, params, default_tz)
    if "CATEGORIES" in props:
        raw = props["CATEGORIES"][1]
        event["categories"] = [
            _unescape(c).strip() for c in raw.split(",") if c.strip()
        ]
    if recurrence:
        # Passed through verbatim: Google Calendar understands RRULE/EXDATE/RDATE.
        event["recurrence"] = list(recurrence)
    return event


def parse_ical(
    text: str,
    default_tz: str = DEFAULT_TIMEZONE,
    on_warning: WarningSink = _ignore_warning,
) -> list[Event]:
    """Parses iCal text into Google Calendar API compatible event dicts."""
    lines = _unfold_lines(text)
    default_tz = _calendar_timezone(lines, default_tz)

    events: list[Event] = []
    skipped = 0
    in_event = False
    props: dict[str, tuple[dict[str, str], str]] = {}
    recurrence: list[str] = []

    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event, props, recurrence = True, {}, []
        elif upper == "END:VEVENT":
            if in_event:
                try:
                    event = _build_event(props, recurrence, default_tz)
                except ValueError:
                    skipped += 1
                    uid = props["UID"][1].strip()
                    on_warning(
                        f"Skipping malformed event (UID '{uid}'): bad date format."
                    )
                else:
                    if event is not None:
                        events.append(event)
            in_event, props, recurrence = False, {}, []
        elif in_event:
            parsed = _parse_property(line)
            if parsed:
                name, params, value = parsed
                if name in RECURRENCE_PROPERTIES:
                    recurrence.append(line)
                elif name not in props:  # keep the first occurrence
                    props[name] = (params, value)

    if skipped:
        on_warning(f"Skipped {skipped} malformed event(s) from the iCal feed.")
    return events


Fetcher = Callable[[str], str]


class IcalFeedSource:
    """Events of an iCal feed URL (the URL is a secret: never logged)."""

    def __init__(
        self,
        url: str,
        on_warning: WarningSink = _ignore_warning,
        fetch: Fetcher = fetch_ical,
        default_tz: str = DEFAULT_TIMEZONE,
    ) -> None:
        self.url = url
        self._on_warning = on_warning
        self._fetch = fetch
        self._default_tz = default_tz

    @property
    def label(self) -> str:
        return "iCal feed"

    def fetch_events(self) -> list[Event]:
        return parse_ical(self._fetch(self.url), self._default_tz, self._on_warning)
