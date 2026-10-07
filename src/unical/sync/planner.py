"""Pure computation of the changes needed to mirror source into target."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from unical.events import Event, summary_of
from unical.strategies import EventColoringStrategy
from unical.sync.models import (
    ColorOrigin,
    EventDecision,
    Mutation,
    MutationAction,
    SyncPlan,
)

MANAGED_PROPERTY = "calendar_coloring_managed"
"""Private property that tags the target events created by this tool."""
LEGACY_MANAGED_PROPERTIES = ("polimi_sync_managed",)
"""Tags of older versions: their events are still managed, and get the current
tag at the next sync."""
MAX_EVENT_ID_LENGTH = 1024
_BASE32HEX = frozenset("abcdefghijklmnopqrstuv0123456789")
_DIGEST_LENGTH = 8

TitleFor = Callable[[Event], str]
"""Title of a source event in the target calendar."""
InScope = Callable[[Event], bool]
"""True if a source event is part of the sync. The others are left alone."""


def sanitize_event_id(raw_id: str) -> str:
    """Makes an ID valid for Google Calendar (base32hex: a-v, 0-9, length 5-1024).

    IDs that are already valid (case aside) keep their form. IDs altered by
    stripping get a short content hash appended, so two distinct source IDs can
    never collapse into the same target ID.
    """
    lowered = raw_id.lower()
    cleaned = "".join(c for c in lowered if c in _BASE32HEX)
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
    if len(cleaned) < 5:
        # Legacy prefix kept on purpose: changing it would change the IDs of
        # events already synced, deleting and inserting them again.
        return f"polimi{digest[:16]}"
    if cleaned != lowered or len(cleaned) > MAX_EVENT_ID_LENGTH:
        keep = MAX_EVENT_ID_LENGTH - _DIGEST_LENGTH
        return f"{cleaned[:keep]}{digest[:_DIGEST_LENGTH]}"
    return cleaned


def _private_properties(event: Event) -> dict[str, Any]:
    return dict((event.get("extendedProperties") or {}).get("private") or {})


def is_managed(event: Event) -> bool:
    """True if the target event was created by this tool (any version)."""
    private = _private_properties(event)
    return any(
        private.get(tag) == "true"
        for tag in (MANAGED_PROPERTY, *LEGACY_MANAGED_PROPERTIES)
    )


def has_current_tag(event: Event) -> bool:
    """True if the target event carries the current tag, and only that one."""
    private = _private_properties(event)
    return private.get(MANAGED_PROPERTY) == "true" and not any(
        tag in private for tag in LEGACY_MANAGED_PROPERTIES
    )


def is_recurring_instance(event: Event) -> bool:
    """True for an occurrence/exception of a recurring event (owned by its master)."""
    return bool(event.get("recurringEventId"))


def _when(event: Event, field_name: str) -> Any:
    value = event.get(field_name) or {}
    return value.get("dateTime", value.get("date"))


def start_day(event: Event) -> date | None:
    """The calendar day an event starts on, if parseable."""
    raw = _when(event, "start")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def build_target_event(
    source: Event, event_id: str, summary: str, color_id: str | None
) -> Event:
    body: Event = {
        "id": event_id,
        "summary": summary,
        "description": source.get("description", ""),
        "start": source.get("start"),
        "end": source.get("end"),
        "extendedProperties": {"private": {MANAGED_PROPERTY: "true"}},
    }
    if "location" in source:
        body["location"] = source["location"]
    if "recurrence" in source:
        body["recurrence"] = source["recurrence"]
    if color_id:
        body["colorId"] = color_id
    return body


def needs_update(existing: Event, desired: Event) -> bool:
    """True if ``existing`` differs from ``desired`` in any synced field.

    Missing text fields equal empty ones: Google omits empty fields from
    responses, and treating them as different would update events every run.
    """
    for text_field in ("summary", "description", "location"):
        if (existing.get(text_field) or "") != (desired.get(text_field) or ""):
            return True
    for field_name in ("colorId", "recurrence"):
        if existing.get(field_name) != desired.get(field_name):
            return True
    if not has_current_tag(existing):
        # Adopts an event with the same ID, or replaces a legacy tag.
        return True
    return any(
        _when(existing, edge) != _when(desired, edge) for edge in ("start", "end")
    )


class SyncPlanner:
    def __init__(
        self,
        strategy: EventColoringStrategy,
        title_for: TitleFor = summary_of,
        prune_before: date | None = None,
        in_scope: InScope | None = None,
    ) -> None:
        """
        :param prune_before: source events starting before this day are left out
            of the target calendar (their managed copies get deleted).
        :param in_scope: tells the source events the sync covers. The others are
            ignored: their copies in the target calendar are never inserted,
            updated nor deleted. Without it, every event is covered.
        """
        self.strategy = strategy
        self.title_for = title_for
        self.prune_before = prune_before
        self.in_scope = in_scope

    def is_pruned(self, event: Event) -> bool:
        if self.prune_before is None:
            return False
        day = start_day(event)
        return day is not None and day < self.prune_before

    def is_syncable(self, event: Event) -> bool:
        """True if the source event will be mirrored into the target calendar."""
        return bool(event.get("id") and event.get("start")) and not self.is_pruned(
            event
        )

    def syncable(self, source_events: Sequence[Event]) -> list[Event]:
        return [event for event in source_events if self.is_syncable(event)]

    def plan(
        self,
        source_events: Sequence[Event],
        target_events: Sequence[Event],
        target_calendar_id: str | None,
    ) -> SyncPlan:
        targets_by_id = {
            e["id"]: e
            for e in target_events
            if "id" in e and not is_recurring_instance(e)
        }
        source_ids: set[str] = set()
        ignored_ids: set[str] = set()
        pruned_ids: set[str] = set()
        skipped_without_start: list[str] = []
        mutations: list[Mutation] = []
        decisions: list[EventDecision] = []

        for source in source_events:
            raw_id = source.get("id")
            if not raw_id:
                continue
            if not source.get("start"):
                skipped_without_start.append(summary_of(source) or raw_id)
                continue
            event_id = sanitize_event_id(raw_id)
            if self.in_scope is not None and not self.in_scope(source):
                ignored_ids.add(event_id)
                continue
            if self.is_pruned(source):
                pruned_ids.add(event_id)
                continue
            source_ids.add(event_id)

            decision, mutation = self._plan_event(
                source, event_id, targets_by_id.get(event_id)
            )
            decisions.append(decision)
            if mutation is not None:
                mutations.append(mutation)

        preserved_unmanaged: list[str] = []
        pruned_count = 0
        for event_id, existing in targets_by_id.items():
            if event_id in source_ids or event_id in ignored_ids:
                continue
            if not is_managed(existing):
                preserved_unmanaged.append(summary_of(existing) or event_id)
                continue
            if self.in_scope is not None and event_id not in pruned_ids:
                # Gone from the source: its kind is unknown, so it may be out
                # of scope. Only a sync of everything cleans it up.
                continue
            if event_id in pruned_ids or self.is_pruned(existing):
                pruned_count += 1
            mutations.append(
                Mutation(
                    action=MutationAction.DELETE,
                    event_id=event_id,
                    summary=existing.get("summary", event_id),
                )
            )

        return SyncPlan(
            target_calendar_id=target_calendar_id,
            mutations=tuple(mutations),
            decisions=tuple(decisions),
            preserved_unmanaged=tuple(preserved_unmanaged),
            skipped_without_start=tuple(skipped_without_start),
            pruned=pruned_count,
            prune_before=self.prune_before,
            source_event_count=len(source_events),
            target_event_count=len(target_events),
        )

    def _plan_event(
        self, source: Event, event_id: str, existing: Event | None
    ) -> tuple[EventDecision, Mutation | None]:
        source_summary = summary_of(source)
        summary = self.title_for(source)
        color_id, origin = self._choose_color(source, existing)
        desired = build_target_event(source, event_id, summary, color_id)

        action: MutationAction | None
        if existing is None:
            action = MutationAction.INSERT
        elif needs_update(existing, desired):
            action = MutationAction.UPDATE
        else:
            action = None

        decision = EventDecision(
            event_id=event_id,
            source_summary=source_summary,
            target_summary=summary,
            color_id=color_id,
            color_origin=origin,
            action=action,
        )
        mutation = (
            Mutation(action=action, event_id=event_id, summary=summary, body=desired)
            if action is not None
            else None
        )
        return decision, mutation

    def _choose_color(
        self, source: Event, existing: Event | None
    ) -> tuple[str | None, ColorOrigin]:
        color = self.strategy.determine_color(source)
        if color is not None:
            return color.color_id, ColorOrigin.STRATEGY
        if existing is not None and "colorId" in existing:
            # Never wipe a color when no strategy applies (e.g. target "exams"
            # keeps lecture colors untouched).
            return existing["colorId"], ColorOrigin.PRESERVED
        return None, ColorOrigin.DEFAULT
