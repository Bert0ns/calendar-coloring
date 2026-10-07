"""Where source events come from (a Google calendar or an iCal feed)."""

from __future__ import annotations

from typing import Protocol

from unical.events import Event
from unical.sync.gateway import CalendarGateway


class SourceError(RuntimeError):
    """The source events cannot be loaded."""


class SourceCalendarNotFoundError(SourceError, LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Source calendar '{name}' not found.")
        self.name = name


class EventSource(Protocol):
    @property
    def label(self) -> str:
        """Human readable, secret-free description (e.g. for logs)."""
        ...

    def fetch_events(self) -> list[Event]:
        """Returns the source events. Raises :class:`SourceError` on failure."""
        ...


class GoogleCalendarSource:
    """Events of a (read-only) Google calendar, found by name."""

    def __init__(self, gateway: CalendarGateway, name: str) -> None:
        self.gateway = gateway
        self.name = name

    @property
    def label(self) -> str:
        return f"'{self.name}'"

    def fetch_events(self) -> list[Event]:
        calendar_id = self.gateway.get_calendar_id_by_name(self.name)
        if not calendar_id:
            raise SourceCalendarNotFoundError(self.name)
        return self.gateway.get_all_events(calendar_id)
