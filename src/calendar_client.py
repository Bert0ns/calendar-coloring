from typing import Any

from googleapiclient.discovery import build


class CalendarClient:
    """Wrapper for Google Calendar API interactions."""

    def __init__(self, credentials: Any) -> None:
        self.service = build("calendar", "v3", credentials=credentials)

    def get_calendar_id_by_name(self, name: str) -> str | None:
        """Finds a calendar ID by its summary (name)."""
        page_token: str | None = None
        while True:
            calendar_list = (
                self.service.calendarList().list(pageToken=page_token).execute()
            )
            for calendar_list_entry in calendar_list.get("items", []):
                if calendar_list_entry.get("summary") == name:
                    return calendar_list_entry["id"]
            page_token = calendar_list.get("nextPageToken")
            if not page_token:
                break
        return None

    def create_calendar(self, name: str, time_zone: str = "Europe/Rome") -> str:
        """Creates a new calendar and returns its ID."""
        calendar = {"summary": name, "timeZone": time_zone}
        created_calendar = self.service.calendars().insert(body=calendar).execute()
        return created_calendar["id"]

    def get_all_events(self, calendar_id: str) -> list[dict[str, Any]]:
        """Fetches all events from a specific calendar."""
        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            events_result = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            events.extend(events_result.get("items", []))
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break
        return events

    def insert_event(
        self, calendar_id: str, event_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Inserts a new event into the calendar."""
        return (
            self.service.events()
            .insert(calendarId=calendar_id, body=event_body)
            .execute()
        )

    def update_event(
        self, calendar_id: str, event_id: str, event_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Updates an entire event."""
        return (
            self.service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=event_body)
            .execute()
        )

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Deletes an event from the calendar."""
        self.service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    def batch_mutate_events(
        self,
        operations: list[dict[str, Any]],
        batch_size: int = 50,
    ) -> list[tuple[dict[str, Any], Exception | None]]:
        """
        Executes mutations (insert, update, delete) in batches of up to `batch_size`.
        Each operation is a dict:
          - {"action": "insert", "calendar_id": ..., "body": ...}
          - {"action": "update", "calendar_id": ..., "event_id": ..., "body": ...}
          - {"action": "delete", "calendar_id": ..., "event_id": ...}

        Returns a list of tuples: (operation, exception_or_None).
        """
        results: list[tuple[dict[str, Any], Exception | None]] = []
        if not operations:
            return results

        for i in range(0, len(operations), batch_size):
            chunk = operations[i : i + batch_size]
            chunk_results: dict[str, tuple[dict[str, Any], Exception | None]] = {}

            def make_callback(
                op_id: str,
                op_dict: dict[str, Any],
                target_results: dict[
                    str, tuple[dict[str, Any], Exception | None]
                ] = chunk_results,
            ):
                def callback(
                    request_id: str, response: Any, exception: Exception | None
                ) -> None:
                    target_results[op_id] = (op_dict, exception)

                return callback

            batch = self.service.new_batch_http_request()
            for idx, op in enumerate(chunk):
                req_id = f"op_{idx}"
                action = op.get("action")
                cal_id = op["calendar_id"]

                if action == "insert":
                    req = self.service.events().insert(
                        calendarId=cal_id, body=op["body"]
                    )
                elif action == "update":
                    req = self.service.events().update(
                        calendarId=cal_id, eventId=op["event_id"], body=op["body"]
                    )
                elif action == "delete":
                    req = self.service.events().delete(
                        calendarId=cal_id, eventId=op["event_id"]
                    )
                else:
                    raise ValueError(f"Unknown mutation action: {action}")

                batch.add(req, callback=make_callback(req_id, op), request_id=req_id)

            try:
                batch.execute()
            except Exception:
                import time

                time.sleep(1)
                try:
                    batch.execute()
                except Exception as exc:
                    for op in chunk:
                        results.append((op, exc))
                    continue
            for idx, op in enumerate(chunk):
                req_id = f"op_{idx}"
                results.append(
                    chunk_results.get(
                        req_id, (op, RuntimeError("Batch request missing result"))
                    )
                )

        return results
