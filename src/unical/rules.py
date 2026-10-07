"""Classification rules: how a university calendar formats its events.

Universities name their events differently (``"Lezione: Didattica - …"``,
``"[CS101] Lecture"``, …), so what makes an event an exam, a lecture or a
deadline is data, stored in the profile. This is the only module that reads
event titles, descriptions and categories to tell them apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum

from unical.events import (
    Enrollment,
    Event,
    ExamOccurrence,
    categories_of,
    description_of,
    start_date,
    summary_of,
)

NAME_GROUP = "name"
"""Regex group that captures the course/exam/deadline name (``(?P<name>…)``)."""

TITLE_PLACEHOLDER = "{title}"
NAME_PLACEHOLDER = "{name}"


class EventKind(Enum):
    EXAM = "exam"
    LECTURE = "lecture"
    DEADLINE = "deadline"


class Field(Enum):
    """The part of an event a condition looks at."""

    TITLE = "title"
    DESCRIPTION = "description"
    LOCATION = "location"
    CATEGORY = "category"
    """Any of the event's iCal ``CATEGORIES``."""


class MatchKind(Enum):
    STARTS_WITH = "starts_with"
    CONTAINS = "contains"
    EQUALS = "equals"
    REGEX = "regex"


@dataclass(frozen=True)
class Condition:
    """A test on one field of an event, e.g. *title starts with "Esame: "*.

    Raises :class:`ValueError` if ``value`` is empty or an invalid regex.
    """

    field: Field
    match: MatchKind
    value: str
    ignore_case: bool = False
    pattern: re.Pattern[str] = dataclass_field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("the value to match is empty")
        literal = re.escape(self.value)
        source = {
            MatchKind.STARTS_WITH: f"^{literal}",
            MatchKind.CONTAINS: literal,
            MatchKind.EQUALS: f"^{literal}$",
            MatchKind.REGEX: self.value,
        }[self.match]
        try:
            pattern = re.compile(source, re.IGNORECASE if self.ignore_case else 0)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from None
        object.__setattr__(self, "pattern", pattern)

    def search(self, event: Event) -> re.Match[str] | None:
        """The first match in the event, or ``None`` if the condition is false."""
        for text in self._texts(event):
            found = self.pattern.search(text)
            if found is not None:
                return found
        return None

    def _texts(self, event: Event) -> Iterator[str]:
        if self.field is Field.TITLE:
            yield summary_of(event)
        elif self.field is Field.DESCRIPTION:
            yield description_of(event)
        elif self.field is Field.LOCATION:
            yield event.get("location") or ""
        else:
            yield from categories_of(event)


@dataclass(frozen=True)
class Rule:
    """Events matching ``condition`` are of the given ``kind``.

    The event's *name* (its course, exam or deadline) is the ``name`` regex
    group if any, else the title without the matched text when the condition
    is on the title, else the whole title. ``title`` is the template of the
    title written to the target calendar: ``{title}`` is the original title
    and ``{name}`` the name.
    """

    kind: EventKind
    condition: Condition
    title: str = TITLE_PLACEHOLDER


@dataclass(frozen=True)
class EnrollmentRules:
    """How exam descriptions tell whether the student is enrolled (optional)."""

    enrolled: Condition | None = None
    not_enrolled: Condition | None = None


@dataclass(frozen=True)
class Classification:
    kind: EventKind
    name: str
    title: str
    """Title of the event in the target calendar."""


@dataclass(frozen=True)
class Classifier:
    """Classifies events with ordered rules: the first matching rule wins."""

    rules: Sequence[Rule] = ()
    enrollment_rules: EnrollmentRules = dataclass_field(default_factory=EnrollmentRules)

    def classify(self, event: Event) -> Classification | None:
        for rule in self.rules:
            found = rule.condition.search(event)
            if found is not None:
                name = _name_of(event, rule.condition, found)
                return Classification(
                    kind=rule.kind, name=name, title=_render(rule.title, event, name)
                )
        return None

    def kind_of(self, event: Event) -> EventKind | None:
        classification = self.classify(event)
        return None if classification is None else classification.kind

    def target_title(self, event: Event) -> str:
        classification = self.classify(event)
        return summary_of(event) if classification is None else classification.title

    def enrollment(self, event: Event) -> Enrollment:
        # "not enrolled" first: its text often contains the "enrolled" one.
        not_enrolled = self.enrollment_rules.not_enrolled
        if not_enrolled is not None and not_enrolled.search(event):
            return Enrollment.NOT_ENROLLED
        enrolled = self.enrollment_rules.enrolled
        if enrolled is not None and enrolled.search(event):
            return Enrollment.ENROLLED
        return Enrollment.UNKNOWN

    def name_of(self, event: Event, kind: EventKind) -> str | None:
        """The name of the event if it is of ``kind``, else ``None``."""
        classification = self.classify(event)
        if classification is None or classification.kind is not kind:
            return None
        return classification.name

    def course_name(self, event: Event) -> str | None:
        return self.name_of(event, EventKind.LECTURE)

    def deadline_name(self, event: Event) -> str | None:
        return self.name_of(event, EventKind.DEADLINE)

    def exam_occurrence(self, event: Event) -> ExamOccurrence | None:
        title = self.name_of(event, EventKind.EXAM)
        if title is None:
            return None
        return ExamOccurrence(
            title=title, date=start_date(event), enrollment=self.enrollment(event)
        )


def _name_of(event: Event, condition: Condition, found: re.Match[str]) -> str:
    title = summary_of(event).strip()
    if NAME_GROUP in found.re.groupindex:
        captured = (found.group(NAME_GROUP) or "").strip()
        return captured or title
    if condition.field is Field.TITLE:
        rest = (found.string[: found.start()] + found.string[found.end() :]).strip()
        return rest or title
    return title


def _render(template: str, event: Event, name: str) -> str:
    # Plain replacement, not str.format: titles may contain braces.
    rendered = template.replace(NAME_PLACEHOLDER, name).replace(
        TITLE_PLACEHOLDER, summary_of(event)
    )
    return rendered if rendered.strip() else summary_of(event)
