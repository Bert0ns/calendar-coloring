import hashlib
import sys
from typing import Any

from calendar_client import CalendarClient
from colors import Colors
from ical_source import fetch_ical_events
from strategies import LectureColoringStrategy


class CalendarSyncProcessor:
    """Coordinates syncing from a read-only calendar to a colored writable calendar."""

    def __init__(
        self,
        client: CalendarClient,
        strategy: Any,
        source_name: str,
        target_name: str,
        verbose: bool = False,
        source_ical_url: str | None = None,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.source_name = source_name
        self.target_name = target_name
        self.verbose = verbose
        self.source_ical_url = source_ical_url

    def log(self, message: str) -> None:
        """Prints message only if verbose mode is enabled."""
        if self.verbose:
            print(message)

    def _sanitize_event_id(self, raw_id: str) -> str:
        """
        Sanitizes an event ID for Google Calendar (base32hex: a-v, 0-9, length 5-1024).
        """
        valid_chars = set("abcdefghijklmnopqrstuv0123456789")
        cleaned = "".join(c for c in raw_id.lower() if c in valid_chars)
        if len(cleaned) < 5:
            h = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
            return f"polimi{h[:16]}"
        return cleaned

    def _clean_summary(self, raw_summary: str) -> str:
        """
        Removes known boilerplate prefixes (e.g. 'Lezione: Didattica - ')
        leaving only the clean course name for the target calendar.
        """
        prefix = LectureColoringStrategy.PREFIX
        if raw_summary.startswith(prefix):
            return raw_summary.removeprefix(prefix).strip()
        return raw_summary

    def process(self) -> None:
        if self.source_ical_url:
            print(
                f"\n{Colors.OKCYAN}{Colors.BOLD}🔍 Syncing iCal feed ➔ '{self.target_name}'{Colors.ENDC}"
            )
            source_events = fetch_ical_events(self.source_ical_url)
        else:
            print(
                f"\n{Colors.OKCYAN}{Colors.BOLD}🔍 Syncing '{self.source_name}' ➔ '{self.target_name}'{Colors.ENDC}"
            )

            source_id = self.client.get_calendar_id_by_name(self.source_name)
            if not source_id:
                print(
                    f"{Colors.FAIL}✖ Source calendar '{self.source_name}' not found.{Colors.ENDC}"
                )
                sys.exit(1)
            source_events = self.client.get_all_events(source_id)

        target_id = self.client.get_calendar_id_by_name(self.target_name)
        if not target_id:
            print(
                f"{Colors.WARNING}⚠ Target calendar not found. Creating '{self.target_name}'...{Colors.ENDC}"
            )
            target_id = self.client.create_calendar(self.target_name)

        target_events = self.client.get_all_events(target_id)
        target_events_map: dict[str, dict[str, Any]] = {
            e["id"]: e for e in target_events if "id" in e
        }

        print(
            f"{Colors.OKBLUE}📥 Fetched {len(source_events)} source & {len(target_events)} target events.{Colors.ENDC}"
        )

        source_event_ids: set[str] = set()
        mutations: list[dict[str, Any]] = []

        for s_event in source_events:
            event_id = s_event.get("id")
            if not event_id:
                continue

            valid_id = self._sanitize_event_id(event_id)
            source_event_ids.add(valid_id)

            raw_summary = s_event.get("summary", "")
            self.log(f"\n{Colors.BOLD}Event: {raw_summary}{Colors.ENDC}")

            # Determine new color
            new_color_id = self.strategy.determine_color(s_event)

            # Clean summary for target calendar
            cleaned_summary = self._clean_summary(raw_summary)
            if cleaned_summary != raw_summary:
                self.log(
                    f" ↳ {Colors.OKBLUE}Cleaned title: '{raw_summary}' ➔ '{cleaned_summary}'{Colors.ENDC}"
                )

            # Construct body for target event with private property tag
            t_body: dict[str, Any] = {
                "id": valid_id,
                "summary": cleaned_summary,
                "description": s_event.get("description", ""),
                "start": s_event.get("start"),
                "end": s_event.get("end"),
                "extendedProperties": {
                    "private": {
                        "polimi_sync_managed": "true",
                    }
                },
            }
            if "location" in s_event:
                t_body["location"] = s_event["location"]

            if new_color_id:
                self.log(
                    f" ↳ {Colors.OKCYAN}Strategy matched (colorId: {new_color_id}){Colors.ENDC}"
                )
                t_body["colorId"] = new_color_id
            elif (
                valid_id in target_events_map
                and "colorId" in target_events_map[valid_id]
            ):
                # CRITICAL: If the strategy doesn't enforce a color (e.g. user skipped lectures),
                # preserve the existing color so we don't accidentally wipe it!
                preserved_color = target_events_map[valid_id]["colorId"]
                self.log(
                    f" ↳ {Colors.OKBLUE}Strategy skipped, preserving existing color (colorId: {preserved_color}){Colors.ENDC}"
                )
                t_body["colorId"] = preserved_color
            else:
                self.log(f" ↳ {Colors.OKBLUE}Using default calendar color{Colors.ENDC}")

            if valid_id in target_events_map:
                t_event = target_events_map[valid_id]
                needs_update = False

                if t_event.get("summary", "") != t_body.get("summary", ""):
                    needs_update = True
                if t_event.get("description", "") != t_body.get("description", ""):
                    needs_update = True
                if t_event.get("location", "") != t_body.get("location", ""):
                    needs_update = True
                if t_event.get("colorId") != t_body.get("colorId"):
                    needs_update = True

                # Ensure managed tag is populated on existing events
                is_managed = (
                    t_event.get("extendedProperties", {})
                    .get("private", {})
                    .get("polimi_sync_managed")
                    == "true"
                )
                if not is_managed:
                    needs_update = True

                s_start = t_body.get("start", {}).get(
                    "dateTime", t_body.get("start", {}).get("date")
                )
                t_start = t_event.get("start", {}).get(
                    "dateTime", t_event.get("start", {}).get("date")
                )
                if s_start != t_start:
                    needs_update = True

                s_end = t_body.get("end", {}).get(
                    "dateTime", t_body.get("end", {}).get("date")
                )
                t_end = t_event.get("end", {}).get(
                    "dateTime", t_event.get("end", {}).get("date")
                )
                if s_end != t_end:
                    needs_update = True

                if needs_update:
                    mutations.append(
                        {
                            "action": "update",
                            "calendar_id": target_id,
                            "event_id": valid_id,
                            "body": t_body,
                            "summary": cleaned_summary,
                        }
                    )
                else:
                    self.log(f" ↳ {Colors.WARNING}Identical (Skipped){Colors.ENDC}")
            else:
                mutations.append(
                    {
                        "action": "insert",
                        "calendar_id": target_id,
                        "body": t_body,
                        "summary": cleaned_summary,
                    }
                )

        # Delete events that no longer exist in source, ONLY if managed by this sync
        for t_event_id, t_event in target_events_map.items():
            if t_event_id not in source_event_ids:
                is_managed = (
                    t_event.get("extendedProperties", {})
                    .get("private", {})
                    .get("polimi_sync_managed")
                    == "true"
                )
                if not is_managed:
                    self.log(
                        f" ↳ {Colors.OKBLUE}Preserving unmanaged target event '{t_event.get('summary')}' (ID: {t_event_id}){Colors.ENDC}"
                    )
                    continue

                mutations.append(
                    {
                        "action": "delete",
                        "calendar_id": target_id,
                        "event_id": t_event_id,
                        "summary": t_event.get("summary", t_event_id),
                    }
                )

        inserted = 0
        updated = 0
        deleted = 0

        if mutations:
            print(
                f"\n{Colors.OKBLUE}⚡ Executing {len(mutations)} calendar operations via batch API...{Colors.ENDC}"
            )
            batch_results = self.client.batch_mutate_events(mutations)
            for op, exc in batch_results:
                action = op["action"]
                summary = op.get("summary", "")
                if exc:
                    print(
                        f"{Colors.FAIL}✖ Failed to {action} '{summary}' - {exc}{Colors.ENDC}"
                    )
                else:
                    if action == "insert":
                        inserted += 1
                        self.log(
                            f" ↳ {Colors.OKGREEN}Inserted into target calendar{Colors.ENDC}"
                        )
                    elif action == "update":
                        updated += 1
                        self.log(
                            f" ↳ {Colors.OKGREEN}Updated in target calendar{Colors.ENDC}"
                        )
                    elif action == "delete":
                        deleted += 1
                        self.log(
                            f" ↳ {Colors.FAIL}Deleted old event ID '{op.get('event_id')}'{Colors.ENDC}"
                        )

        print(f"\n{Colors.OKGREEN}{Colors.BOLD}✔ Finished Sync!{Colors.ENDC}")
        print(f"  Inserted: {Colors.OKGREEN}{inserted}{Colors.ENDC}")
        print(f"  Updated:  {Colors.WARNING}{updated}{Colors.ENDC}")
        print(f"  Deleted:  {Colors.FAIL}{deleted}{Colors.ENDC}\n")
