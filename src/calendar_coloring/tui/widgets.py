"""Reusable TUI pieces: color swatches, the color and calendar pickers and the
plan view."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Tree
from textual.widgets.option_list import Option

from calendar_coloring.events import Event
from calendar_coloring.palette import GoogleColor
from calendar_coloring.sync.models import (
    ColorOrigin,
    EventDecision,
    Mutation,
    MutationAction,
    SyncPlan,
)
from calendar_coloring.tui.setup import CalendarChoice

ACTION_STYLES = {
    MutationAction.INSERT: ("+", "Insert", "green"),
    MutationAction.UPDATE: ("~", "Update", "yellow"),
    MutationAction.DELETE: ("-", "Delete", "red"),
}

_COLOR_ORIGINS = {
    ColorOrigin.STRATEGY: "from your preferences",
    ColorOrigin.PRESERVED: "kept from the target event",
    ColorOrigin.DEFAULT: "calendar default",
}


def swatch(color: GoogleColor | None, label: bool = True) -> Text:
    """A truecolor block followed by the color name (``—`` for no color)."""
    if color is None:
        return Text("   — none", style="dim")
    text = Text()
    text.append("   ", style=f"on {color.hex}")
    if label:
        text.append(f" {color.label}")
    return text


class ColorPicker(ModalScreen[GoogleColor | None]):
    """Modal list of the 11 Google colors. Dismisses with the chosen color, or
    ``None`` when cancelled."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ColorPicker {
        align: center middle;
    }
    ColorPicker > Vertical {
        width: 44;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    ColorPicker OptionList {
        height: auto;
        max-height: 15;
    }
    """

    def __init__(self, subject: str, current: GoogleColor | None = None) -> None:
        super().__init__()
        self.subject = subject
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text.assemble("Color for ", (self.subject, "bold")))
            yield OptionList(
                *(
                    Option(
                        swatch(color).append(f"  ({color.color_id})", style="dim"),
                        id=color.color_id,
                    )
                    for color in GoogleColor
                )
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        if self.current is not None:
            options.highlighted = list(GoogleColor).index(self.current)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(GoogleColor.from_id(str(event.option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)


class CalendarPicker(ModalScreen[str | None]):
    """Modal list of calendars. With ``validate_new``, a new calendar name can
    also be typed. Dismisses with the chosen name, or ``None`` when cancelled."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    CalendarPicker {
        align: center middle;
    }
    CalendarPicker > Vertical {
        width: 64;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    CalendarPicker OptionList {
        height: auto;
        max-height: 16;
    }
    CalendarPicker #calendar-error {
        color: $error;
    }
    """

    def __init__(
        self,
        title: str,
        choices: Sequence[CalendarChoice],
        validate_new: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.choices = list(choices)
        self.validate_new = validate_new

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.title_text, style="bold"))
            if self.choices:
                yield OptionList(
                    *(
                        Option(
                            Text.assemble(
                                choice.name,
                                (f"  {choice.note}" if choice.note else "", "dim"),
                            ),
                            id=str(index),
                            disabled=not choice.allowed,
                        )
                        for index, choice in enumerate(self.choices)
                    )
                )
            else:
                yield Label(Text("No calendars found.", style="dim"))
            if self.validate_new is not None:
                yield Input(
                    placeholder="…or type the name of a new calendar",
                    id="new-calendar",
                )
                yield Label("", id="calendar-error")

    def on_mount(self) -> None:
        current = next(
            (i for i, c in enumerate(self.choices) if c.note.startswith("current")),
            None,
        )
        if self.choices:
            options = self.query_one(OptionList)
            if current is not None:
                options.highlighted = current
            options.focus()
        elif self.validate_new is not None:
            self.query_one(Input).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.choices[int(str(event.option.id))].name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        assert self.validate_new is not None
        error = self.validate_new(event.value)
        if error is not None:
            self.query_one("#calendar-error", Label).update(error)
            return
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


def plan_summary(plan: SyncPlan) -> Text:
    text = Text()
    for action, (sign, label, style) in ACTION_STYLES.items():
        text.append(f"{sign} {plan.count(action)} {label.lower()}  ", style=style)
    text.append(f"· {plan.unchanged} unchanged", style="dim")
    notes = []
    if plan.target_calendar_id is None:
        notes.append("The target calendar will be created.")
    if plan.pruned:
        notes.append(
            f"{plan.pruned} event(s) before {plan.prune_before} will be pruned."
        )
    if plan.preserved_unmanaged:
        notes.append(
            f"{len(plan.preserved_unmanaged)} event(s) you added yourself are "
            "left untouched."
        )
    if plan.skipped_without_start:
        notes.append(
            f"{len(plan.skipped_without_start)} source event(s) without a start "
            "time are skipped."
        )
    for note in notes:
        text.append(f"\n{note}", style="italic")
    return text


def _when(event: Event) -> str:
    def edge(name: str) -> str:
        value = event.get(name) or {}
        return str(value.get("dateTime", value.get("date", "?")))

    return f"{edge('start')} → {edge('end')}"


def _mutation_details(mutation: Mutation, decision: EventDecision | None) -> list[Text]:
    body = mutation.body or {}
    color = GoogleColor.parse(body.get("colorId"))
    origin = _COLOR_ORIGINS[decision.color_origin] if decision else ""
    details = [
        Text.assemble("Color: ", swatch(color), (f"  {origin}", "dim")),
        Text(f"When: {_when(body)}"),
    ]
    if decision is not None and decision.source_summary != decision.target_summary:
        details.append(Text(f"Source title: {decision.source_summary}"))
    if body.get("location"):
        details.append(Text(f"Location: {body['location']}"))
    if body.get("recurrence"):
        details.append(Text("Recurring event"))
    return details


class PlanTree(Tree[None]):
    """The mutations of a plan grouped by action, expandable per event."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__("Changes", id=id)
        self.show_root = False

    def show_plan(self, plan: SyncPlan) -> None:
        self.clear()
        self.root.expand()
        decisions = {d.event_id: d for d in plan.decisions}
        for action, (sign, label, style) in ACTION_STYLES.items():
            mutations = [m for m in plan.mutations if m.action is action]
            if not mutations:
                continue
            group = self.root.add(
                Text(f"{sign} {label} ({len(mutations)})", style=f"bold {style}"),
                expand=True,
            )
            for mutation in mutations:
                if action is MutationAction.DELETE:
                    group.add_leaf(mutation.summary)
                    continue
                node = group.add(mutation.summary)
                for detail in _mutation_details(
                    mutation, decisions.get(mutation.event_id)
                ):
                    node.add_leaf(detail)
