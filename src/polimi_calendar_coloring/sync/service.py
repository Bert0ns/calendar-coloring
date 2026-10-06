"""Orchestrates target-calendar I/O around the pure planner."""

from __future__ import annotations

from polimi_calendar_coloring.events import Event
from polimi_calendar_coloring.sync.gateway import CalendarGateway
from polimi_calendar_coloring.sync.models import SyncPlan, SyncResult
from polimi_calendar_coloring.sync.planner import SyncPlanner


class SyncService:
    def __init__(self, gateway: CalendarGateway) -> None:
        self.gateway = gateway

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

    def apply(self, plan: SyncPlan, target_name: str) -> SyncResult:
        """Executes the plan, creating the target calendar if it does not exist."""
        calendar_id = plan.target_calendar_id
        created = False
        if not calendar_id:
            calendar_id = self.gateway.create_calendar(target_name)
            created = True
        results = (
            self.gateway.batch_mutate_events(calendar_id, plan.mutations)
            if plan.mutations
            else []
        )
        return SyncResult(results=tuple(results), created_target_calendar=created)
