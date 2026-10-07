"""Application use case: discover → preferences → plan → apply.

:meth:`SyncWorkflow.run` executes every phase in one go (the CLI). Frontends
that let the user edit preferences between phases (the TUI) drive them one at
a time: :meth:`~SyncWorkflow.load`, :meth:`~SyncWorkflow.preview`, then
:meth:`~SyncWorkflow.complete_preferences`, :meth:`~SyncWorkflow.save_preferences`
and :meth:`~SyncWorkflow.apply`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from calendar_coloring.catalog import Catalog, discover
from calendar_coloring.events import Event
from calendar_coloring.preferences import Preferences
from calendar_coloring.profile import Profile
from calendar_coloring.reporting import Reporter
from calendar_coloring.resolution import fill_missing_preferences
from calendar_coloring.strategies import strategy_for
from calendar_coloring.sync.models import CalendarInfo, SyncPlan, SyncResult
from calendar_coloring.sync.planner import SyncPlanner
from calendar_coloring.sync.service import ProgressCallback, SyncService
from calendar_coloring.sync.source import EventSource
from calendar_coloring.targets import SyncTarget


class ProfileRepository(Protocol):
    def load(self) -> Profile: ...

    def save(self, profile: Profile) -> None: ...


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
class SyncSession:
    """Result of the discover phase: what is in the source, and the profile to
    classify and color it with (mutable, edited in place by frontends)."""

    options: SyncOptions
    target_name: str
    source_events: list[Event]
    catalog: Catalog
    profile: Profile

    @property
    def preferences(self) -> Preferences:
        return self.profile.preferences


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
        repository: ProfileRepository,
        reporter: Reporter,
        editor: PreferenceEditor | None = None,
    ) -> None:
        self.service = service
        self.repository = repository
        self.reporter = reporter
        self.editor = editor

    def run(
        self, options: SyncOptions, source: EventSource, target_name: str
    ) -> SyncOutcome:
        """Runs a sync. With ``dry_run`` nothing is written (calendar or files).

        Raises :class:`SourceError` if the source events cannot be loaded.
        """
        session = self.load(options, source, target_name)
        self._update_preferences(session)
        if not options.dry_run:
            self.save_preferences(session)

        plan = self.plan(session)
        if options.dry_run:
            self.reporter.dry_run_finished(plan)
            return SyncOutcome(plan=plan, result=None)
        return SyncOutcome(plan=plan, result=self.apply(session, plan))

    # -- phases --------------------------------------------------------------

    def load(
        self,
        options: SyncOptions,
        source: EventSource,
        target_name: str,
        profile: Profile | None = None,
    ) -> SyncSession:
        """Discover phase: fetches the source events and the saved profile.

        A frontend that already holds a profile (possibly with unsaved edits)
        passes it to discover the events with it instead.

        Raises :class:`SourceError` if the source events cannot be loaded.
        """
        self.reporter.sync_started(source.label, target_name)
        source_events = source.fetch_events()
        if profile is None:
            profile = self.repository.load()
        catalog = discover(
            self._planner(options, profile).syncable(source_events),
            profile.classifier,
        )
        return SyncSession(
            options=options,
            target_name=target_name,
            source_events=source_events,
            catalog=catalog,
            profile=profile,
        )

    def syncable_events(self, session: SyncSession) -> list[Event]:
        """The source events that are copied to the target calendar."""
        return self._planner(session.options, session.profile).syncable(
            session.source_events
        )

    def rediscover(self, session: SyncSession) -> SyncSession:
        """The session with its events classified again, after the profile's
        rules changed. No I/O."""
        return replace(
            session,
            catalog=discover(self.syncable_events(session), session.profile.classifier),
        )

    def complete_preferences(self, session: SyncSession) -> None:
        """Fills preferences the user never chose with the automatic rules."""
        fill_missing_preferences(
            session.catalog, session.preferences, session.options.target
        )

    def save_preferences(self, session: SyncSession) -> None:
        self.repository.save(session.profile)

    def load_profile(self) -> Profile:
        return self.repository.load()

    def save_profile(self, profile: Profile) -> None:
        self.repository.save(profile)

    def list_calendars(self) -> list[CalendarInfo]:
        """The user's calendars, to choose the source and the target from."""
        return self.service.list_calendars()

    def plan(self, session: SyncSession) -> SyncPlan:
        """Computes the changes for the session preferences, as they are. Read-only."""
        target_id = self.service.find_target_calendar(session.target_name)
        if target_id is None:
            self.reporter.target_calendar_missing(
                session.target_name, session.options.dry_run
            )
        plan = self.service.plan(
            self._planner(session.options, session.profile),
            session.source_events,
            target_id,
        )
        self.reporter.plan_ready(plan)
        return plan

    def apply(
        self,
        session: SyncSession,
        plan: SyncPlan,
        on_progress: ProgressCallback | None = None,
    ) -> SyncResult:
        self.reporter.applying(plan)
        result = self.service.apply(
            plan,
            session.target_name,
            on_progress,
            time_zone=session.profile.calendars.time_zone,
        )
        self.reporter.sync_finished(result)
        return result

    def preview(self, session: SyncSession) -> SyncPlan:
        """The plan that applies once missing preferences are completed.

        They are completed on a copy, so the session keeps telling apart what
        the user chose from what the automatic rules fill in.
        """
        draft = replace(
            session,
            profile=replace(
                session.profile, preferences=copy.deepcopy(session.preferences)
            ),
        )
        self.complete_preferences(draft)
        return self.plan(draft)

    # -- helpers -------------------------------------------------------------

    def _planner(self, options: SyncOptions, profile: Profile) -> SyncPlanner:
        # Strategies read preferences lazily, so they see later edits.
        classifier = profile.classifier
        return SyncPlanner(
            strategy_for(options.target, profile.preferences, classifier),
            classifier.target_title,
            prune_before=options.prune_before,
        )

    def _update_preferences(self, session: SyncSession) -> None:
        if session.options.interactive:
            if self.editor is None:
                raise ValueError("Interactive mode requires a PreferenceEditor")
            self.editor.edit(
                session.catalog, session.preferences, session.options.target
            )
        else:
            self.complete_preferences(session)
