"""Console implementation of :class:`Reporter`."""

from __future__ import annotations

from collections.abc import Callable

from unical.cli.ansi import (
    BLUE,
    BOLD,
    CYAN,
    GREEN,
    RED,
    YELLOW,
    rgb,
    style,
)
from unical.palette import GoogleColor
from unical.sync.models import (
    ColorOrigin,
    EventDecision,
    MutationAction,
    SyncPlan,
    SyncResult,
)
from unical.version_check import format_upgrade_notice

_ACTION_STYLE = {
    MutationAction.INSERT: ("+ insert", GREEN),
    MutationAction.UPDATE: ("~ update", YELLOW),
    MutationAction.DELETE: ("- delete", RED),
}


def _color_label(color_id: str | None) -> str:
    if color_id is None:
        return "calendar default"
    color = GoogleColor.parse(color_id)
    if color is None:
        return f"colorId {color_id}"
    return style(f"{color.label} ({color_id})", rgb(color))


class ConsoleReporter:
    """Prints progress. ``quiet`` hides everything but warnings and errors;
    ``verbose`` adds per-event details."""

    def __init__(
        self,
        verbose: bool = False,
        quiet: bool = False,
        write: Callable[[str], None] = print,
    ) -> None:
        self.verbose = verbose and not quiet
        self.quiet = quiet
        self._write = write

    def _out(self, text: str) -> None:
        if not self.quiet:
            self._write(text)

    # -- generic messages ----------------------------------------------------

    def detail(self, message: str) -> None:
        if self.verbose:
            self._write(message)

    def info(self, message: str) -> None:
        self._out(style(message, BLUE))

    def warning(self, message: str) -> None:
        self._write(style(f"⚠ {message}", YELLOW))

    def error(self, message: str) -> None:
        self._write(style(f"✖ {message}", RED))

    def update_available(self, current: str, latest: str) -> None:
        if not self.quiet:
            self._write("\n" + format_upgrade_notice(current, latest) + "\n")

    # -- sync lifecycle ------------------------------------------------------

    def sync_started(self, source_label: str, target_name: str) -> None:
        self._out(
            "\n" + style(f"🔍 Syncing {source_label} ➔ '{target_name}'", CYAN, BOLD)
        )

    def target_calendar_missing(self, target_name: str, dry_run: bool) -> None:
        verb = "Would create" if dry_run else "Creating"
        self._out(
            style(f"⚠ Target calendar not found. {verb} '{target_name}'...", YELLOW)
        )

    def plan_ready(self, plan: SyncPlan) -> None:
        for summary in plan.skipped_without_start:
            self.warning(f"Skipping '{summary}': no start time.")
        self.info(
            f"📥 Fetched {plan.source_event_count} source & "
            f"{plan.target_event_count} target events."
        )
        if plan.pruned:
            self.info(
                f"🧹 Pruning {plan.pruned} managed event(s) starting before "
                f"{plan.prune_before}."
            )
        if not self.verbose:
            return
        for decision in plan.decisions:
            self._write_decision(decision)
        for summary in plan.preserved_unmanaged:
            self._write(
                style(f"  ↳ Preserving unmanaged target event '{summary}'", BLUE)
            )
        for mutation in plan.mutations:
            if mutation.action is MutationAction.DELETE:
                self._write(style(f"  - delete stale event '{mutation.summary}'", RED))

    def applying(self, plan: SyncPlan) -> None:
        if not plan.is_empty:
            self._out(
                "\n"
                + style(
                    f"⚡ Executing {len(plan.mutations)} calendar operations "
                    "via batch API...",
                    BLUE,
                )
            )

    def sync_finished(self, result: SyncResult) -> None:
        for failure in result.failures:
            mutation = failure.mutation
            self.error(
                f"Failed to {mutation.action.value} '{mutation.summary}' - "
                f"{failure.error}"
            )
        headline = (
            style("✔ Finished Sync!", GREEN, BOLD)
            if result.succeeded
            else style(
                f"✖ Finished Sync with {len(result.failures)} failure(s).", RED, BOLD
            )
        )
        self._out("\n" + headline)
        self._out(f"  Inserted: {style(str(result.inserted), GREEN)}")
        self._out(f"  Updated:  {style(str(result.updated), YELLOW)}")
        self._out(f"  Deleted:  {style(str(result.deleted), RED)}\n")

    def dry_run_finished(self, plan: SyncPlan) -> None:
        self._out(
            "\n"
            + style(
                f"🔎 DRY RUN — no changes applied. Would execute "
                f"{len(plan.mutations)} operation(s):",
                YELLOW,
                BOLD,
            )
        )
        for mutation in plan.mutations:
            label, color_code = _ACTION_STYLE[mutation.action]
            self._out(f"  {style(label, color_code)} {mutation.summary}")
        self._out(
            f"\n  Would insert: {style(str(plan.count(MutationAction.INSERT)), GREEN)}"
        )
        self._out(
            f"  Would update: {style(str(plan.count(MutationAction.UPDATE)), YELLOW)}"
        )
        self._out(
            f"  Would delete: {style(str(plan.count(MutationAction.DELETE)), RED)}"
        )
        self._out(f"  Unchanged:    {plan.unchanged}\n")

    # -- helpers -------------------------------------------------------------

    def _write_decision(self, decision: EventDecision) -> None:
        self._write("\n" + style(f"Event: {decision.source_summary}", BOLD))
        if decision.target_summary != decision.source_summary:
            self._write(
                style(
                    f"  ↳ Cleaned title: '{decision.source_summary}' ➔ "
                    f"'{decision.target_summary}'",
                    BLUE,
                )
            )
        color = _color_label(decision.color_id)
        if decision.color_origin is ColorOrigin.STRATEGY:
            self._write(f"  ↳ Strategy matched: {color}")
        elif decision.color_origin is ColorOrigin.PRESERVED:
            self._write(f"  ↳ Strategy skipped, preserving existing color: {color}")
        else:
            self._write("  ↳ Using default calendar color")
        if decision.action is None:
            self._write(style("  ↳ Identical (skipped)", YELLOW))
        else:
            label, color_code = _ACTION_STYLE[decision.action]
            self._write(style(f"  ↳ {label}", color_code))
