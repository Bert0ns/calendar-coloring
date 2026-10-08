"""Custom events defined in user profile or adopted from Google Calendar."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from unical.events import Event, summary_of

CUSTOM_PROPERTY = "calendar_coloring_custom"
MANAGED_PROPERTY = "calendar_coloring_managed"


def _extract_start_day(start_dict: dict[str, Any]) -> date | None:
    raw = start_dict.get("dateTime", start_dict.get("date"))
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def sanitize_custom_id(raw_id: str | None = None) -> str:
    """Generates or sanitizes a valid base32hex event ID for Google Calendar."""
    if not raw_id:
        random_suffix = uuid.uuid4().hex[:16]
        return f"cst{random_suffix}"
    lowered = raw_id.lower()
    cleaned = "".join(c for c in lowered if c in "abcdefghijklmnopqrstuv0123456789")
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
    if len(cleaned) < 5:
        return f"cst{digest[:16]}"
    if cleaned != lowered or len(cleaned) > 1024:
        return f"{cleaned[:1000]}{digest[:8]}"
    return cleaned


@dataclass(frozen=True)
class CustomEvent:
    """A user-defined custom event, stored in profile.json and mirrored to Google Calendar."""

    id: str
    summary: str
    start: dict[str, Any]
    end: dict[str, Any]
    description: str = ""
    location: str = ""
    color_id: str | None = None
    recurrence: tuple[str, ...] = ()
    source: str = "created"  # "created" | "adopted"

    @property
    def start_day(self) -> date | None:
        return _extract_start_day(self.start)

    def to_target_event(self) -> Event:
        """Converts to a Google Calendar event resource with managed and custom tags."""
        body: Event = {
            "id": self.id,
            "summary": self.summary,
            "description": self.description,
            "start": dict(self.start),
            "end": dict(self.end),
            "extendedProperties": {
                "private": {
                    MANAGED_PROPERTY: "true",
                    CUSTOM_PROPERTY: "true",
                }
            },
        }
        if self.location:
            body["location"] = self.location
        if self.recurrence:
            body["recurrence"] = list(self.recurrence)
        if self.color_id:
            body["colorId"] = self.color_id
        return body


def adopt_target_event(target_event: Event) -> CustomEvent:
    """Adopts an existing unmanaged Google Calendar event into a CustomEvent."""
    raw_id = target_event.get("id") or ""
    # Existing target events already have a valid Google Calendar ID.
    # Preserve it exactly so SyncPlanner can update it in-place without duplicating it.
    custom_id = raw_id if raw_id else sanitize_custom_id()
    summary = summary_of(target_event) or "Custom Event"
    description = target_event.get("description") or ""
    location = target_event.get("location") or ""
    color_id = target_event.get("colorId")
    recurrence_raw = target_event.get("recurrence") or []
    recurrence = tuple(str(r) for r in recurrence_raw)

    start = dict(target_event.get("start") or {})
    end = dict(target_event.get("end") or {})
    if not start:
        today_iso = date.today().isoformat()
        start = {"date": today_iso}
    if not end:
        if "date" in start:
            try:
                start_d = date.fromisoformat(str(start["date"]))
                end = {"date": (start_d + timedelta(days=1)).isoformat()}
            except ValueError:
                end = dict(start)
        elif "dateTime" in start:
            try:
                start_dt = datetime.fromisoformat(str(start["dateTime"]))
                end = {"dateTime": (start_dt + timedelta(hours=1)).isoformat()}
            except ValueError:
                end = dict(start)
        else:
            end = dict(start)

    return CustomEvent(
        id=custom_id,
        summary=summary,
        description=description,
        location=location,
        color_id=color_id,
        start=start,
        end=end,
        recurrence=recurrence,
        source="adopted",
    )


def is_custom_event(event: Event) -> bool:
    """True if the target event is tagged as a unical custom event."""
    private = dict((event.get("extendedProperties") or {}).get("private") or {})
    return private.get(CUSTOM_PROPERTY) == "true"
