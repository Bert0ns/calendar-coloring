"""Value objects describing a sync plan and its outcome."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from unical.events import Event


@dataclass(frozen=True)
class CalendarInfo:
    """A calendar of the user's calendar list."""

    id: str
    name: str
    writable: bool
    """True if events can be added to it (owner or writer access)."""
    primary: bool = False


class MutationAction(Enum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class Mutation:
    action: MutationAction
    event_id: str
    summary: str
    body: Event | None = None
    """Full target event resource (``None`` for deletions)."""


class ColorOrigin(Enum):
    STRATEGY = "strategy"
    """A coloring strategy chose the color."""
    PRESERVED = "preserved"
    """No strategy matched: the color already on the target event is kept."""
    DEFAULT = "default"
    """No color: the event uses the calendar's default color."""


@dataclass(frozen=True)
class EventDecision:
    """What the planner decided for a single source event (for reporting)."""

    event_id: str
    source_summary: str
    target_summary: str
    color_id: str | None
    color_origin: ColorOrigin
    action: MutationAction | None
    """``None`` when the target event is already up to date."""


@dataclass(frozen=True)
class SyncPlan:
    target_calendar_id: str | None
    """``None`` when the target calendar does not exist yet."""
    mutations: tuple[Mutation, ...] = ()
    decisions: tuple[EventDecision, ...] = ()
    preserved_unmanaged: tuple[str, ...] = ()
    """Summaries of target events not created by this tool (never touched)."""
    skipped_without_start: tuple[str, ...] = ()
    """Summaries of source events ignored because they have no start time."""
    pruned: int = 0
    """How many of the deletions are due to ``prune_before``."""
    prune_before: date | None = None
    source_event_count: int = 0
    target_event_count: int = 0

    def count(self, action: MutationAction) -> int:
        return sum(1 for m in self.mutations if m.action is action)

    @property
    def unchanged(self) -> int:
        return sum(1 for d in self.decisions if d.action is None)

    @property
    def is_empty(self) -> bool:
        return not self.mutations


@dataclass(frozen=True)
class MutationResult:
    mutation: Mutation
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SyncResult:
    results: tuple[MutationResult, ...] = ()
    created_target_calendar: bool = False

    def count(self, action: MutationAction) -> int:
        """Number of *successful* mutations of the given kind."""
        return sum(1 for r in self.results if r.ok and r.mutation.action is action)

    @property
    def inserted(self) -> int:
        return self.count(MutationAction.INSERT)

    @property
    def updated(self) -> int:
        return self.count(MutationAction.UPDATE)

    @property
    def deleted(self) -> int:
        return self.count(MutationAction.DELETE)

    @property
    def failures(self) -> tuple[MutationResult, ...]:
        return tuple(r for r in self.results if not r.ok)

    @property
    def succeeded(self) -> bool:
        return not self.failures
