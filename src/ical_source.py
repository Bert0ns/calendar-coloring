"""Fetches and parses iCal feeds into Google Calendar event dicts.

Supports the Polimi iCal format (flat VEVENTs, floating Europe/Rome times)
using only the standard library, so no new dependencies are required.
"""

import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Rome"


def fetch_ical(url: str, timeout: int = 30) -> str:
    """Downloads raw iCal text from a URL."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
    name = parts[0].upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, _, val = part.partition("=")
            params[key.upper()] = val.strip('"')
    return name, params, value


def _parse_datetime(
    value: str, params: dict[str, str], default_tz: str
) -> tuple[dict[str, str], str]:
    """Converts an iCal DTSTART/DTEND value to a Google Calendar time dict.

    Returns (google_time_dict, sort_key) where sort_key is used for nothing
    but kept simple; the dict is either {"date": ...} or
    {"dateTime": ..., "timeZone": ...}.
    """
    value_type = params.get("VALUE", "").upper()
    tzid = params.get("TZID", default_tz)

    if value_type == "DATE" or (len(value) == 8 and "T" not in value):
        parsed_date = date(
            int(value[0:4]), int(value[4:6]), int(value[6:8])
        ).isoformat()
        return {"date": parsed_date}, parsed_date

    # DATE-TIME: YYYYMMDDTHHMMSS with optional trailing Z (UTC)
    is_utc = value.endswith("Z")
    core = value[:-1] if is_utc else value
    dt = datetime.strptime(core, "%Y%m%dT%H%M%S")

    if is_utc:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        tzid = "UTC"
    else:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzid))
        except Exception:
            dt = dt.replace(tzinfo=ZoneInfo(default_tz))
            tzid = default_tz

    return {"dateTime": dt.isoformat(), "timeZone": tzid}, dt.isoformat()


def parse_ical(
    text: str, default_tz: str = DEFAULT_TIMEZONE
) -> list[dict[str, object]]:
    """Parses iCal text into Google Calendar API compatible event dicts."""
    # Calendar-level default timezone (X-WR-TIMEZONE) wins over fallback
    unfolded = _unfold_lines(text)
    for line in unfolded:
        if line.upper().startswith("X-WR-TIMEZONE"):
            parsed = _parse_property(line)
            if parsed and parsed[2].strip():
                default_tz = parsed[2].strip()
            break

    events: list[dict[str, object]] = []
    in_event = False
    props: dict[str, tuple[dict[str, str], str]] = {}

    def flush_event() -> None:
        uid_prop = props.get("UID")
        if not uid_prop:
            return
        uid = uid_prop[1].strip()
        if not uid:
            return

        def text_of(name: str) -> str:
            return _unescape(props[name][1]) if name in props else ""

        event: dict[str, object] = {
            "id": uid,
            "summary": text_of("SUMMARY"),
        }
        description = text_of("DESCRIPTION")
        if description:
            event["description"] = description
        location = text_of("LOCATION")
        if location:
            event["location"] = location

        if "DTSTART" in props:
            params, value = props["DTSTART"]
            event["start"], _ = _parse_datetime(value, params, default_tz)
        if "DTEND" in props:
            params, value = props["DTEND"]
            event["end"], _ = _parse_datetime(value, params, default_tz)
        events.append(event)

    for line in unfolded:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            props = {}
        elif upper == "END:VEVENT":
            if in_event:
                flush_event()
            in_event = False
            props = {}
        elif in_event:
            parsed = _parse_property(line)
            if parsed:
                name, params, value = parsed
                # Keep first occurrence of each property
                if name not in props:
                    props[name] = (params, value)

    return events


def fetch_ical_events(
    url: str, timeout: int = 30, default_tz: str = DEFAULT_TIMEZONE
) -> list[dict[str, object]]:
    """Fetches an iCal URL and returns Google Calendar compatible events."""
    return parse_ical(fetch_ical(url, timeout=timeout), default_tz=default_tz)
