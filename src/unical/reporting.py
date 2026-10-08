"""Output port: the core reports progress here instead of printing."""

from __future__ import annotations

from typing import Protocol

from unical.sync.models import SyncPlan, SyncResult


class Reporter(Protocol):
    def detail(self, message: str) -> None:
        """Verbose-only information."""
        ...

    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...

    def sync_started(self, source_label: str, target_name: str) -> None: ...

    def target_calendar_missing(self, target_name: str, dry_run: bool) -> None: ...

    def plan_ready(self, plan: SyncPlan) -> None: ...

    def applying(self, plan: SyncPlan) -> None: ...

    def sync_finished(self, result: SyncResult) -> None: ...

    def dry_run_finished(self, plan: SyncPlan) -> None: ...

    def update_available(self, current: str, latest: str) -> None: ...


class NullReporter:
    """Reporter that discards everything (useful in tests and scripts)."""

    def detail(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def sync_started(self, source_label: str, target_name: str) -> None:
        pass

    def target_calendar_missing(self, target_name: str, dry_run: bool) -> None:
        pass

    def plan_ready(self, plan: SyncPlan) -> None:
        pass

    def applying(self, plan: SyncPlan) -> None:
        pass

    def sync_finished(self, result: SyncResult) -> None:
        pass

    def dry_run_finished(self, plan: SyncPlan) -> None:
        pass

    def update_available(self, current: str, latest: str) -> None:
        pass
