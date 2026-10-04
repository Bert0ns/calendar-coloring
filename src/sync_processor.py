import hashlib
import sys
from datetime import date
from typing import Any

from calendar_client import CalendarClient
from colors import Colors
from ical_source import ICalError, fetch_ical_events
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
        dry_run: bool = False,
        quiet: bool = False,
        prune_before: date | None = None,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.source_name = source_name
        self.target_name = target_name
        self.verbose = verbose
        self.source_ical_url = source_ical_url
        self.dry_run = dry_run
        self.quiet = quiet
        self.prune_before = prune_before

    def log(self, message: str) -> None:
        """Prints message only if verbose mode is enabled."""
        if self.verbose and not self.quiet:
            print(message)

    def info(self, message: str) -> None:
        """Prints informational message unless quiet mode is enabled."""
        if not self.quiet:
            print(message)

    def _sanitize_event_id(self, raw_id: str) -> str:
        """
        Sanitizes an event ID for Google Calendar (base32hex: a-v, 0-9, length 5-1024).

        IDs that survive stripping unchanged keep their form (backward compatible).
        IDs altered by stripping get a short content hash appended so two distinct
        source IDs can never collapse into one target ID.
        """
        valid_chars = set("abcdefghijklmnopqrstuv0123456789")
        lowered = raw_id.lower()
        cleaned = "".join(c for c in lowered if c in valid_chars)
        if len(cleaned) < 5:
            h = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
            return f"polimi{h[:16]}"
        if cleaned != lowered:
            digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:8]
            return f"{cleaned[:1016]}{digest}"
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

    def _fetch_source_events(self) -> list[dict[str, Any]]:
        """Loads source events from the iCal feed or the Google source calendar."""
        if self.source_ical_url:
            self.info(
                f"\n{Colors.OKCYAN}{Colors.BOLD}🔍 Syncing iCal feed ➔ '{self.target_name}'{Colors.ENDC}"
            )
            try:
                return fetch_ical_events(self.source_ical_url)
            except ICalError as exc:
                print(f"{Colors.FAIL}✖ {exc}{Colors.ENDC}")
                sys.exit(1)

        self.info(
            f"\n{Colors.OKCYAN}{Colors.BOLD}🔍 Syncing '{self.source_name}' ➔ '{self.target_name}'{Colors.ENDC}"
        )
        source_id = self.client.get_calendar_id_by_name(self.source_name)
        if not source_id:
            print(
                f"{Colors.FAIL}✖ Source calendar '{self.source_name}' not found.{Colors.ENDC}"
            )
            sys.exit(1)
        return self.client.get_all_events(source_id)

    def _ensure_target_calendar(self) -> str:
        """Returns the target calendar ID, creating the calendar if needed."""
        target_id = self.client.get_calendar_id_by_name(self.target_name)
        if not target_id:
            self.info(
                f"{Colors.WARNING}⚠ Target calendar not found. Creating '{self.target_name}'...{Colors.ENDC}"
            )
            target_id = self.client.create_calendar(self.target_name)
        return target_id

    @staticmethod
    def _is_managed(event: dict[str, Any]) -> bool:
        """Whether the event was created by this sync (safe to update/delete)."""
        return (
            event.get("extendedProperties", {})
            .get("private", {})
            .get("polimi_sync_managed")
            == "true"
        )

    @staticmethod
    def _start_date(event: dict[str, Any]) -> date | None:
        """Extracts the calendar date an event starts on, if parseable."""
        start = event.get("start", {})
        raw = start.get("dateTime", start.get("date"))
        if not raw:
            return None
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None

    def _build_event_body(
        self,
        s_event: dict[str, Any],
        valid_id: str,
        target_events_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Builds the target-calendar body for one source event."""
        raw_summary = s_event.get("summary", "")

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
        if "recurrence" in s_event:
            t_body["recurrence"] = s_event["recurrence"]

        if new_color_id:
            self.log(
                f" ↳ {Colors.OKCYAN}Strategy matched (colorId: {new_color_id}){Colors.ENDC}"
            )
            t_body["colorId"] = new_color_id
        elif valid_id in target_events_map and "colorId" in target_events_map[valid_id]:
            # CRITICAL: If the strategy doesn't enforce a color (e.g. user skipped lectures),
            # preserve the existing color so we don't accidentally wipe it!
            preserved_color = target_events_map[valid_id]["colorId"]
            self.log(
                f" ↳ {Colors.OKBLUE}Strategy skipped, preserving existing color (colorId: {preserved_color}){Colors.ENDC}"
            )
            t_body["colorId"] = preserved_color
        else:
            self.log(f" ↳ {Colors.OKBLUE}Using default calendar color{Colors.ENDC}")

        return t_body

    def _needs_update(self, t_event: dict[str, Any], t_body: dict[str, Any]) -> bool:
        """Whether the target event differs from the desired body."""
        for field in ("summary", "description", "location", "colorId", "recurrence"):
            if t_event.get(field) != t_body.get(field):
                return True

        # Ensure managed tag is populated on existing events
        if not self._is_managed(t_event):
            return True

        for edge in ("start", "end"):
            s_val = (t_body.get(edge) or {}).get(
                "dateTime", (t_body.get(edge) or {}).get("date")
            )
            t_val = (t_event.get(edge) or {}).get(
                "dateTime", (t_event.get(edge) or {}).get("date")
            )
            if s_val != t_val:
                return True

        return False

    def _build_mutations(
        self,
        source_events: list[dict[str, Any]],
        target_events_map: dict[str, dict[str, Any]],
        target_id: str,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Computes insert/update/delete operations to align target with source."""
        mutations: list[dict[str, Any]] = []
        source_event_ids: set[str] = set()

        for s_event in source_events:
            event_id = s_event.get("id")
            if not event_id:
                continue
            if not s_event.get("start"):
                print(
                    f"{Colors.WARNING}⚠ Skipping '{s_event.get('summary', event_id)}': no start time.{Colors.ENDC}"
                )
                continue

            valid_id = self._sanitize_event_id(event_id)
            source_event_ids.add(valid_id)

            self.log(f"\n{Colors.BOLD}Event: {s_event.get('summary', '')}{Colors.ENDC}")
            t_body = self._build_event_body(s_event, valid_id, target_events_map)

            if valid_id in target_events_map:
                if self._needs_update(target_events_map[valid_id], t_body):
                    mutations.append(
                        {
                            "action": "update",
                            "calendar_id": target_id,
                            "event_id": valid_id,
                            "body": t_body,
                            "summary": t_body.get("summary", ""),
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
                        "summary": t_body.get("summary", ""),
                    }
                )

        # Delete events that no longer exist in source, ONLY if managed by this sync
        for t_event_id, t_event in target_events_map.items():
            if t_event_id not in source_event_ids:
                if not self._is_managed(t_event):
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

        return mutations, source_event_ids

    def _build_prune_mutations(
        self,
        target_events_map: dict[str, dict[str, Any]],
        target_id: str,
    ) -> list[dict[str, Any]]:
        """Deletes managed target events starting before the prune cutoff."""
        assert self.prune_before is not None
        mutations: list[dict[str, Any]] = []
        for t_event_id, t_event in target_events_map.items():
            if not self._is_managed(t_event):
                continue
            start = self._start_date(t_event)
            if start is not None and start < self.prune_before:
                mutations.append(
                    {
                        "action": "delete",
                        "calendar_id": target_id,
                        "event_id": t_event_id,
                        "summary": t_event.get("summary", t_event_id),
                    }
                )
        return mutations

    def _apply_mutations(self, mutations: list[dict[str, Any]]) -> None:
        """Executes mutations via the batch API (or just reports them in dry-run)."""
        if self.dry_run:
            self.info(
                f"\n{Colors.WARNING}🔎 DRY RUN — no changes applied. "
                f"Would execute {len(mutations)} operation(s):{Colors.ENDC}"
            )
        elif mutations:
            self.info(
                f"\n{Colors.OKBLUE}⚡ Executing {len(mutations)} calendar operations via batch API...{Colors.ENDC}"
            )

        inserted = 0
        updated = 0
        deleted = 0

        if self.dry_run:
            for op in mutations:
                action = op["action"]
                self.info(f"  [{action}] {op.get('summary', op.get('event_id'))}")
                if action == "insert":
                    inserted += 1
                elif action == "update":
                    updated += 1
                elif action == "delete":
                    deleted += 1
        elif mutations:
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

        self.info(f"\n{Colors.OKGREEN}{Colors.BOLD}✔ Finished Sync!{Colors.ENDC}")
        self.info(f"  Inserted: {Colors.OKGREEN}{inserted}{Colors.ENDC}")
        self.info(f"  Updated:  {Colors.WARNING}{updated}{Colors.ENDC}")
        self.info(f"  Deleted:  {Colors.FAIL}{deleted}{Colors.ENDC}\n")

    def process(self) -> None:
        source_events = self._fetch_source_events()
        target_id = self._ensure_target_calendar()

        target_events = self.client.get_all_events(target_id)
        target_events_map: dict[str, dict[str, Any]] = {
            e["id"]: e for e in target_events if "id" in e
        }

        self.info(
            f"{Colors.OKBLUE}📥 Fetched {len(source_events)} source & {len(target_events)} target events.{Colors.ENDC}"
        )

        mutations, _ = self._build_mutations(
            source_events, target_events_map, target_id
        )

        if self.prune_before is not None:
            pruned = self._build_prune_mutations(target_events_map, target_id)
            # A pruned event may already be scheduled for deletion as stale;
            # never enqueue the same delete twice.
            already_deleting = {
                op["event_id"] for op in mutations if op["action"] == "delete"
            }
            pruned = [op for op in pruned if op["event_id"] not in already_deleting]
            if pruned:
                self.info(
                    f"{Colors.OKBLUE}🧹 Pruning {len(pruned)} managed event(s) starting before {self.prune_before}.{Colors.ENDC}"
                )
            mutations.extend(pruned)

        self._apply_mutations(mutations)
