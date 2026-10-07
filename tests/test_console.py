import re

from unical.cli.ansi import palette_lines
from unical.cli.console import ConsoleReporter
from unical.sync.models import (
    ColorOrigin,
    EventDecision,
    Mutation,
    MutationAction,
    MutationResult,
    SyncPlan,
    SyncResult,
)

ANSI = re.compile(r"\033\[[0-9;]*m")

INSERT = Mutation(MutationAction.INSERT, "e1", "CS", {})
UPDATE = Mutation(MutationAction.UPDATE, "e2", "ML", {})
DELETE = Mutation(MutationAction.DELETE, "e3", "Old")

PLAN = SyncPlan(
    target_calendar_id="tgt",
    mutations=(INSERT, UPDATE, DELETE),
    decisions=(
        EventDecision(
            "e1",
            "Lezione: Didattica - CS",
            "CS",
            "5",
            ColorOrigin.STRATEGY,
            MutationAction.INSERT,
        ),
        EventDecision(
            "e2", "ML", "ML", "9", ColorOrigin.PRESERVED, MutationAction.UPDATE
        ),
        EventDecision("e4", "Other", "Other", None, ColorOrigin.DEFAULT, None),
    ),
    preserved_unmanaged=("Dentist",),
    source_event_count=3,
    target_event_count=4,
)


def capture(
    verbose: bool = False, quiet: bool = False
) -> tuple[ConsoleReporter, list[str]]:
    lines: list[str] = []
    reporter = ConsoleReporter(
        verbose=verbose, quiet=quiet, write=lambda s: lines.append(ANSI.sub("", s))
    )
    return reporter, lines


def test_plan_ready_non_verbose_prints_only_counts() -> None:
    reporter, lines = capture()
    reporter.plan_ready(PLAN)
    assert lines == ["📥 Fetched 3 source & 4 target events."]


def test_plan_ready_verbose_explains_every_decision() -> None:
    reporter, lines = capture(verbose=True)
    reporter.plan_ready(PLAN)
    text = "\n".join(lines)
    assert "Cleaned title: 'Lezione: Didattica - CS' ➔ 'CS'" in text
    assert "Strategy matched: Banana (5)" in text
    assert "preserving existing color: Blueberry (9)" in text
    assert "Using default calendar color" in text
    assert "Identical (skipped)" in text
    assert "Preserving unmanaged target event 'Dentist'" in text
    assert "delete stale event 'Old'" in text


def test_sync_finished_summary() -> None:
    reporter, lines = capture()
    result = SyncResult(
        results=(
            MutationResult(INSERT),
            MutationResult(UPDATE, RuntimeError("quota")),
            MutationResult(DELETE),
        )
    )
    reporter.sync_finished(result)
    text = "\n".join(lines)
    assert "Failed to update 'ML' - quota" in text
    assert "Finished Sync with 1 failure(s)." in text
    assert "Inserted: 1" in text
    assert "Updated:  0" in text
    assert "Deleted:  1" in text


def test_sync_finished_success() -> None:
    reporter, lines = capture()
    reporter.sync_finished(SyncResult())
    assert "✔ Finished Sync!" in "\n".join(lines)


def test_dry_run_summary() -> None:
    reporter, lines = capture()
    reporter.dry_run_finished(PLAN)
    text = "\n".join(lines)
    assert "DRY RUN — no changes applied. Would execute 3 operation(s):" in text
    assert "+ insert CS" in text
    assert "~ update ML" in text
    assert "- delete Old" in text
    assert "Would insert: 1" in text
    assert "Would update: 1" in text
    assert "Would delete: 1" in text
    assert "Unchanged:    1" in text


def test_target_missing_messages() -> None:
    reporter, lines = capture()
    reporter.target_calendar_missing("Tgt", dry_run=True)
    reporter.target_calendar_missing("Tgt", dry_run=False)
    assert "Would create 'Tgt'" in lines[0]
    assert "Creating 'Tgt'" in lines[1]


def test_applying_is_silent_for_empty_plans() -> None:
    reporter, lines = capture()
    reporter.applying(SyncPlan("tgt"))
    assert lines == []
    reporter.applying(PLAN)
    assert "Executing 3 calendar operations" in lines[0]


def test_generic_messages() -> None:
    reporter, lines = capture()
    reporter.info("i")
    reporter.warning("w")
    reporter.error("e")
    assert lines == ["i", "⚠ w", "✖ e"]


def test_palette_lines_render_all_colors() -> None:
    lines = [ANSI.sub("", line) for line in palette_lines()]
    assert len(lines) == 3
    assert lines[0].startswith("1: Lavender")
    assert lines[-1].endswith("11: Tomato")


def test_quiet_hides_everything_but_warnings_and_errors() -> None:
    reporter, lines = capture(verbose=True, quiet=True)
    reporter.detail("d")
    reporter.info("i")
    reporter.sync_started("'Src'", "Tgt")
    reporter.target_calendar_missing("Tgt", dry_run=False)
    reporter.plan_ready(PLAN)
    reporter.applying(PLAN)
    reporter.dry_run_finished(PLAN)
    reporter.sync_finished(SyncResult(results=(MutationResult(INSERT),)))
    assert lines == []

    reporter.warning("w")
    reporter.error("e")
    reporter.sync_finished(
        SyncResult(results=(MutationResult(UPDATE, RuntimeError("quota")),))
    )
    assert lines == ["⚠ w", "✖ e", "✖ Failed to update 'ML' - quota"]


def test_detail_only_in_verbose_mode() -> None:
    reporter, lines = capture()
    reporter.detail("hidden")
    verbose, verbose_lines = capture(verbose=True)
    verbose.detail("shown")
    assert lines == []
    assert verbose_lines == ["shown"]


def test_plan_ready_reports_skipped_and_pruned_events() -> None:
    from datetime import date

    reporter, lines = capture()
    reporter.plan_ready(
        SyncPlan(
            "tgt",
            skipped_without_start=("Broken",),
            pruned=2,
            prune_before=date(2026, 9, 1),
        )
    )
    assert "⚠ Skipping 'Broken': no start time." in lines
    assert "🧹 Pruning 2 managed event(s) starting before 2026-09-01." in lines


def test_sync_started_uses_source_label() -> None:
    reporter, lines = capture()
    reporter.sync_started("iCal feed", "Tgt")
    assert "🔍 Syncing iCal feed ➔ 'Tgt'" in lines[0]
