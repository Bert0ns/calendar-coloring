"""Application use case: discover → preferences → plan → apply."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from polimi_calendar_coloring.catalog import Catalog, discover
from polimi_calendar_coloring.events import clean_summary
from polimi_calendar_coloring.preferences import Preferences
from polimi_calendar_coloring.reporting import Reporter
from polimi_calendar_coloring.resolution import fill_missing_preferences
from polimi_calendar_coloring.strategies import strategy_for
from polimi_calendar_coloring.sync.models import SyncPlan, SyncResult
from polimi_calendar_coloring.sync.planner import SummaryTransform, SyncPlanner
from polimi_calendar_coloring.sync.service import SyncService
from polimi_calendar_coloring.sync.source import EventSource
from polimi_calendar_coloring.targets import SyncTarget


class PreferencesRepository(Protocol):
    def load(self) -> Preferences: ...

    def save(self, preferences: Preferences) -> None: ...


class PreferenceEditor(Protocol):
    """Lets the user review/modify preferences (CLI prompts, TUI, ...)."""

    def edit(
        self, catalog: Catalog, preferences: Preferences, target: SyncTarget
    ) -> None: ...


@dataclass(frozen=True)
class SyncOptions:
    target: SyncTarget = SyncTarget.ALL
    interactive: bool = False
    dry_run: bool = False
    prune_before: date | None = None
    """Leave out (and delete) events starting before this day."""


@dataclass(frozen=True)
class SyncOutcome:
    plan: SyncPlan
    result: SyncResult | None
    """``None`` for dry runs."""

    @property
    def succeeded(self) -> bool:
        return self.result is None or self.result.succeeded


class SyncWorkflow:
    def __init__(
        self,
        service: SyncService,
        repository: PreferencesRepository,
        reporter: Reporter,
        editor: PreferenceEditor | None = None,
        summary_transform: SummaryTransform = clean_summary,
    ) -> None:
        self.service = service
        self.repository = repository
        self.reporter = reporter
        self.editor = editor
        self.summary_transform = summary_transform

    def run(
        self, options: SyncOptions, source: EventSource, target_name: str
    ) -> SyncOutcome:
        """Runs a sync. With ``dry_run`` nothing is written (calendar or files).

        Raises :class:`SourceError` if the source events cannot be loaded.
        """
        self.reporter.sync_started(source.label, target_name)
        source_events = source.fetch_events()

        preferences = self.repository.load()
        # Strategies read preferences lazily, so they see the edits below.
        planner = SyncPlanner(
            strategy_for(options.target, preferences),
            self.summary_transform,
            prune_before=options.prune_before,
        )
        catalog = discover(planner.syncable(source_events))
        self._update_preferences(catalog, preferences, options)
        if not options.dry_run:
            self.repository.save(preferences)

        target_id = self.service.find_target_calendar(target_name)
        if target_id is None:
            self.reporter.target_calendar_missing(target_name, options.dry_run)

        plan = self.service.plan(planner, source_events, target_id)
        self.reporter.plan_ready(plan)

        if options.dry_run:
            self.reporter.dry_run_finished(plan)
            return SyncOutcome(plan=plan, result=None)

        self.reporter.applying(plan)
        result = self.service.apply(plan, target_name)
        self.reporter.sync_finished(result)
        return SyncOutcome(plan=plan, result=result)

    def _update_preferences(
        self, catalog: Catalog, preferences: Preferences, options: SyncOptions
    ) -> None:
        if options.interactive:
            if self.editor is None:
                raise ValueError("Interactive mode requires a PreferenceEditor")
            self.editor.edit(catalog, preferences, options.target)
        else:
            fill_missing_preferences(catalog, preferences, options.target)
