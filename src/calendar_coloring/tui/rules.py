"""Editing of the classification rules. Pure Python: no Textual, no I/O."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from calendar_coloring.events import Event, summary_of
from calendar_coloring.rules import (
    TITLE_PLACEHOLDER,
    Classifier,
    Condition,
    EventKind,
    Field,
    MatchKind,
    Rule,
)

SEPARATORS = (": ", " - ", " – ", " | ", "] ")
"""What usually ends the boilerplate at the start of an event title."""

_FIELD_LABELS = {
    Field.TITLE: "title",
    Field.DESCRIPTION: "description",
    Field.LOCATION: "location",
    Field.CATEGORY: "a category",
}
_MATCH_LABELS = {
    MatchKind.STARTS_WITH: "starts with",
    MatchKind.CONTAINS: "contains",
    MatchKind.EQUALS: "is",
    MatchKind.REGEX: "matches",
}


@dataclass(frozen=True)
class RuleForm:
    """The values of the rule editor: unlike a :class:`Rule`, possibly invalid."""

    kind: EventKind = EventKind.LECTURE
    field: Field = Field.TITLE
    match: MatchKind = MatchKind.STARTS_WITH
    value: str = ""
    ignore_case: bool = False
    title: str = TITLE_PLACEHOLDER

    @classmethod
    def of(cls, rule: Rule) -> RuleForm:
        return cls.of_condition(rule.condition, kind=rule.kind, title=rule.title)

    @classmethod
    def of_condition(
        cls,
        condition: Condition | None,
        kind: EventKind = EventKind.LECTURE,
        title: str = TITLE_PLACEHOLDER,
    ) -> RuleForm:
        if condition is None:
            return cls(kind=kind, title=title)
        return cls(
            kind=kind,
            field=condition.field,
            match=condition.match,
            value=condition.value,
            ignore_case=condition.ignore_case,
            title=title,
        )


@dataclass(frozen=True)
class TitlePreview:
    """How the events with the same title are classified."""

    title: str
    count: int
    kind: EventKind | None
    name: str
    target_title: str


def describe(condition: Condition) -> str:
    """E.g. ``title starts with 'Esame: '``."""
    text = (
        f"{_FIELD_LABELS[condition.field]} {_MATCH_LABELS[condition.match]} "
        f"{condition.value!r}"
    )
    return text + " (ignore case)" if condition.ignore_case else text


def preview(events: Iterable[Event], classifier: Classifier) -> list[TitlePreview]:
    """The classification of each distinct title, in the order of the events.

    Events with the same title may still differ (e.g. in their categories):
    the first one decides.
    """
    counts: Counter[str] = Counter()
    first: dict[str, Event] = {}
    for event in events:
        title = summary_of(event)
        counts[title] += 1
        first.setdefault(title, event)
    previews: list[TitlePreview] = []
    for title, event in first.items():
        classification = classifier.classify(event)
        previews.append(
            TitlePreview(
                title=title,
                count=counts[title],
                kind=classification.kind if classification else None,
                name=classification.name if classification else "",
                target_title=classification.title if classification else title,
            )
        )
    return previews


def kind_counts(previews: Sequence[TitlePreview]) -> dict[EventKind | None, int]:
    """Events per kind (``None``: matched by no rule)."""
    counts: dict[EventKind | None, int] = {kind: 0 for kind in EventKind}
    counts[None] = 0
    for item in previews:
        counts[item.kind] += item.count
    return counts


def summary(previews: Sequence[TitlePreview]) -> str:
    counts = kind_counts(previews)
    parts = [
        f"{counts[EventKind.LECTURE]} lectures",
        f"{counts[EventKind.EXAM]} exams",
        f"{counts[EventKind.DEADLINE]} deadlines",
        f"{counts[None]} unmatched",
    ]
    return " · ".join(parts)


def count_matches(condition: Condition, events: Iterable[Event]) -> int:
    return sum(1 for event in events if condition.search(event) is not None)


def suggest_prefix(title: str, titles: Iterable[str]) -> str:
    """The boilerplate at the start of ``title``, to prefill a new rule.

    It's the longest start shared with the other titles that begin the same
    way, cut after a separator: with ``"Lezione: Didattica - CS"`` and
    ``"Lezione: Didattica - Math"``, it's ``"Lezione: Didattica - "``. A title
    like no other is cut after its first separator; without any separator,
    it's the whole title.
    """
    first = _cut_after_separator(title, last=False)
    if first is None:
        return title
    similar = [other for other in titles if other.startswith(first) and other != title]
    if not similar:
        return first
    common = os.path.commonprefix([title, *similar])
    return _cut_after_separator(common, last=True) or first


def _cut_after_separator(text: str, last: bool) -> str | None:
    ends = [
        text.find(sep) + len(sep) if not last else text.rfind(sep) + len(sep)
        for sep in SEPARATORS
        if sep in text
    ]
    if not ends:
        return None
    return text[: max(ends) if last else min(ends)]
