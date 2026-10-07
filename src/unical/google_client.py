"""Google Calendar API adapter implementing :class:`CalendarGateway`."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from googleapiclient.discovery import build

from unical.events import Event
from unical.sync.models import (
    CalendarInfo,
    Mutation,
    MutationAction,
    MutationResult,
)

FALLBACK_TIME_ZONE = "UTC"
WRITABLE_ACCESS_ROLES = frozenset({"owner", "writer"})
MAX_BATCH_SIZE = 50  # Google recommends at most 50 calls per batch
BATCH_RETRIES = 3


class GoogleCalendarClient:
    def __init__(
        self,
        service: Any,
        sleep: Callable[[float], None] = time.sleep,
        batch_retries: int = BATCH_RETRIES,
    ) -> None:
        self.service = service
        self._sleep = sleep
        self._batch_retries = batch_retries

    @classmethod
    def from_credentials(cls, credentials: Any) -> GoogleCalendarClient:
        return cls(build("calendar", "v3", credentials=credentials))

    def list_calendars(self) -> list[CalendarInfo]:
        return [
            CalendarInfo(
                id=str(entry["id"]),
                name=str(entry.get("summary") or entry["id"]),
                writable=entry.get("accessRole") in WRITABLE_ACCESS_ROLES,
                primary=bool(entry.get("primary", False)),
            )
            for entry in self._calendar_list_entries()
        ]

    def get_calendar_id_by_name(self, name: str) -> str | None:
        """Finds a calendar ID by its summary (name)."""
        for entry in self._calendar_list_entries():
            if entry.get("summary") == name:
                return str(entry["id"])
        return None

    def _calendar_list_entries(self) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            page = self.service.calendarList().list(pageToken=page_token).execute()
            yield from page.get("items", [])
            page_token = page.get("nextPageToken")
            if not page_token:
                return

    def create_calendar(self, name: str, time_zone: str | None = None) -> str:
        """Creates a calendar, in the time zone of the user's primary calendar
        unless ``time_zone`` is given."""
        if time_zone is None:
            primary = self.service.calendars().get(calendarId="primary").execute()
            time_zone = str(primary.get("timeZone") or FALLBACK_TIME_ZONE)
        body = {"summary": name, "timeZone": time_zone}
        return str(self.service.calendars().insert(body=body).execute()["id"])

    def get_all_events(
        self, calendar_id: str, expand_recurring: bool = True
    ) -> list[Event]:
        # orderBy="startTime" is only allowed when expanding recurring events.
        ordering = {"orderBy": "startTime"} if expand_recurring else {}
        events: list[Event] = []
        page_token: str | None = None
        while True:
            page = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=expand_recurring,
                    pageToken=page_token,
                    **ordering,
                )
                .execute()
            )
            events.extend(page.get("items", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return events

    def batch_mutate_events(
        self,
        calendar_id: str,
        mutations: Sequence[Mutation],
        batch_size: int = MAX_BATCH_SIZE,
    ) -> list[MutationResult]:
        """Executes mutations through the batch API, ``batch_size`` at a time."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        results: list[MutationResult] = []
        for start in range(0, len(mutations), batch_size):
            results.extend(
                self._execute_chunk(calendar_id, mutations[start : start + batch_size])
            )
        return results

    def _execute_chunk(
        self, calendar_id: str, chunk: Sequence[Mutation]
    ) -> list[MutationResult]:
        errors: dict[str, Exception | None] = {}

        def make_callback(request_id: str) -> Callable[[str, Any, Any], None]:
            def callback(_: str, __: Any, exception: Exception | None) -> None:
                errors[request_id] = exception

            return callback

        batch = self.service.new_batch_http_request()
        for index, mutation in enumerate(chunk):
            request_id = f"op_{index}"
            batch.add(
                self._request_for(calendar_id, mutation),
                callback=make_callback(request_id),
                request_id=request_id,
            )
        failure = self._execute_with_retries(batch)
        if failure is not None:
            return [MutationResult(mutation, failure) for mutation in chunk]

        results: list[MutationResult] = []
        for index, mutation in enumerate(chunk):
            request_id = f"op_{index}"
            if request_id in errors:
                results.append(MutationResult(mutation, errors[request_id]))
            else:
                missing = RuntimeError("Batch request missing result")
                results.append(MutationResult(mutation, missing))
        return results

    def _execute_with_retries(self, batch: Any) -> Exception | None:
        """Executes a batch, retrying transport-level failures with backoff.

        Returns the last error if every attempt failed, else ``None``.
        """
        last_error: Exception | None = None
        for attempt in range(self._batch_retries + 1):
            if attempt:
                self._sleep(2 ** (attempt - 1))
            try:
                batch.execute()
            except Exception as exc:
                last_error = exc
            else:
                return None
        return last_error

    def _request_for(self, calendar_id: str, mutation: Mutation) -> Any:
        events = self.service.events()
        if mutation.action is MutationAction.INSERT:
            return events.insert(calendarId=calendar_id, body=mutation.body)
        if mutation.action is MutationAction.UPDATE:
            return events.update(
                calendarId=calendar_id, eventId=mutation.event_id, body=mutation.body
            )
        if mutation.action is MutationAction.DELETE:
            return events.delete(calendarId=calendar_id, eventId=mutation.event_id)
        raise ValueError(f"Unknown mutation action: {mutation.action}")
