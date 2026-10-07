"""Orchestrates target-calendar I/O around the pure planner."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from unical.events import Event
from unical.sync.gateway import CalendarGateway
from unical.sync.models import (
    CalendarInfo,
    Mutation,
    MutationResult,
    SyncPlan,
    SyncResult,
)
from unical.sync.planner import SyncPlanner

PROGRESS_CHUNK_SIZE = 50
"""Mutations sent to the gateway per call, so that progress can be reported."""

ProgressCallback = Callable[[int, int], None]
"""Called with ``(completed, total)`` mutations while a plan is being applied."""


class SyncService:
    def __init__(self, gateway: CalendarGateway) -> None:
        self.gateway = gateway

    def list_calendars(self) -> list[CalendarInfo]:
        return self.gateway.list_calendars()

    def find_target_calendar(self, target_name: str) -> str | None:
        return self.gateway.get_calendar_id_by_name(target_name)

    def plan(
        self,
        planner: SyncPlanner,
        source_events: list[Event],
        target_calendar_id: str | None,
    ) -> SyncPlan:
        """Computes the plan. Read-only: a missing target calendar is not created."""
        target_events = (
            # Recurring events are synced as a single master event, so compare
            # against masters rather than their expanded occurrences.
            self.gateway.get_all_events(target_calendar_id, expand_recurring=False)
            if target_calendar_id
            else []
        )
        return planner.plan(source_events, target_events, target_calendar_id)

    def apply(
        self,
        plan: SyncPlan,
        target_name: str,
        on_progress: ProgressCallback | None = None,
        time_zone: str | None = None,
    ) -> SyncResult:
        """Executes the plan, creating the target calendar (in ``time_zone``, if
        given) if it does not exist."""
        calendar_id = plan.target_calendar_id
        created = False
        if not calendar_id:
            calendar_id = self.gateway.create_calendar(target_name, time_zone)
            created = True
        results = self._mutate(calendar_id, plan.mutations, on_progress)
        return SyncResult(results=tuple(results), created_target_calendar=created)

    def _mutate(
        self,
        calendar_id: str,
        mutations: Sequence[Mutation],
        on_progress: ProgressCallback | None,
    ) -> list[MutationResult]:
        results: list[MutationResult] = []
        total = len(mutations)
        for start in range(0, total, PROGRESS_CHUNK_SIZE):
            chunk = mutations[start : start + PROGRESS_CHUNK_SIZE]
            results.extend(self.gateway.batch_mutate_events(calendar_id, chunk))
            if on_progress is not None:
                on_progress(len(results), total)
        return results
