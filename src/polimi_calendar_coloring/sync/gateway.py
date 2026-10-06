from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from polimi_calendar_coloring.events import Event
from polimi_calendar_coloring.sync.models import Mutation, MutationResult


class CalendarGateway(Protocol):
    """Port to a calendar backend (implemented by the Google client)."""

    def get_calendar_id_by_name(self, name: str) -> str | None: ...

    def create_calendar(self, name: str) -> str: ...

    def get_all_events(
        self, calendar_id: str, expand_recurring: bool = True
    ) -> list[Event]:
        """Lists events. With ``expand_recurring=False`` recurring events are
        returned once (as their master event) instead of one per occurrence."""
        ...

    def batch_mutate_events(
        self, calendar_id: str, mutations: Sequence[Mutation]
    ) -> list[MutationResult]:
        """Applies mutations; returns one result per mutation, in order."""
        ...
