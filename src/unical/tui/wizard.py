"""Screens of the first-run setup: a welcome, then a check of the rules.

The calendar steps reuse :class:`~unical.tui.widgets.CalendarPicker`;
the app drives the whole sequence.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

from unical.tui.rules import TitlePreview, kind_counts, summary
from unical.tui.widgets import KIND_LABELS

SAMPLE_SIZE = 8

_WIZARD_CSS = """
{screen} {{
    align: center middle;
}}
{screen} > Vertical {{
    width: 80;
    height: auto;
    max-height: 90%;
    border: thick $accent;
    background: $surface;
    padding: 1 2;
}}
{screen} .wizard-text {{
    margin-bottom: 1;
}}
{screen} Horizontal {{
    height: auto;
}}
{screen} Button {{
    margin-right: 2;
}}
"""


class WizardIntro(ModalScreen[bool]):
    """Welcome screen. Dismisses with ``True`` to start, ``False`` to skip."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "skip", "Skip")]
    DEFAULT_CSS = _WIZARD_CSS.format(screen="WizardIntro")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text("Welcome to Uni Calendar Coloring", style="bold"))
            yield Static(
                "It copies your university timetable into a Google Calendar you "
                "own, with a color for each course, your exams and your "
                "deadlines.\n\nLet's set it up in three steps:\n"
                "  1. the calendar with your timetable,\n"
                "  2. the calendar to write the colored copy to,\n"
                "  3. how to recognize lectures, exams and deadlines.\n\n"
                "Nothing is written to Google Calendar until you apply the "
                "changes. You can change everything later in the Setup and "
                "Rules tabs.",
                classes="wizard-text",
            )
            with Horizontal():
                yield Button("Start", id="wizard-start", variant="primary")
                yield Button("Skip", id="wizard-skip")

    def on_mount(self) -> None:
        self.query_one("#wizard-start", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "wizard-start")

    def action_skip(self) -> None:
        self.dismiss(False)


class RulesChoice(Enum):
    KEEP = "keep"
    EDIT = "edit"
    EMPTY = "empty"


class WizardRules(ModalScreen[RulesChoice]):
    """Shows how the current rules classify the events, and asks what to do."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "keep", "Keep")]
    DEFAULT_CSS = (
        _WIZARD_CSS.format(screen="WizardRules")
        + """
    WizardRules DataTable {
        margin-bottom: 1;
    }
    """
    )

    def __init__(self, profile_name: str, previews: Sequence[TitlePreview]) -> None:
        super().__init__()
        self.profile_name = profile_name or "built-in"
        self.previews = list(previews)

    def compose(self) -> ComposeResult:
        unmatched = kind_counts(self.previews)[None]
        verdict = (
            "Every event is recognized."
            if unmatched == 0
            else f"{unmatched} event(s) match no rule: they're copied without a color."
        )
        with Vertical():
            yield Label(Text("Step 3 · Recognizing your events", style="bold"))
            yield Static(
                f"With the {self.profile_name} rules: {summary(self.previews)}. "
                f"{verdict}",
                classes="wizard-text",
            )
            table: DataTable[Text] = DataTable(cursor_type="none", zebra_stripes=True)
            table.add_columns("Kind", "Event title")
            for item in self._sample():
                kind = (
                    Text(KIND_LABELS[item.kind])
                    if item.kind is not None
                    else Text("unmatched", style="yellow")
                )
                table.add_row(kind, Text(item.title))
            table.styles.height = table.row_count + 1  # the header
            yield table
            with Horizontal():
                yield Button("Keep these rules", id="rules-keep", variant="primary")
                yield Button("Adjust them", id="rules-edit")
                yield Button("Start from scratch", id="rules-empty")

    def _sample(self) -> list[TitlePreview]:
        """Unmatched titles first: they're the ones to look at."""
        ordered = sorted(self.previews, key=lambda item: item.kind is not None)
        return ordered[:SAMPLE_SIZE]

    def on_mount(self) -> None:
        self.query_one("#rules-keep", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(RulesChoice(str(event.button.id).removeprefix("rules-")))

    def action_keep(self) -> None:
        self.dismiss(RulesChoice.KEEP)
