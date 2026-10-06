"""Built-in rules for the calendar formats we know about."""

from __future__ import annotations

from calendar_coloring.rules import (
    NAME_PLACEHOLDER,
    Condition,
    EnrollmentRules,
    EventKind,
    Field,
    MatchKind,
    Rule,
)

POLIMI_NAME = "Politecnico di Milano"


def _title_starts_with(prefix: str) -> Condition:
    return Condition(Field.TITLE, MatchKind.STARTS_WITH, prefix)


def _category(name: str) -> Condition:
    return Condition(Field.CATEGORY, MatchKind.EQUALS, name)


# iCal feeds may omit the title prefixes but tag events with CATEGORIES.
POLIMI_RULES = (
    Rule(EventKind.EXAM, _title_starts_with("Esame: ")),
    Rule(EventKind.EXAM, _category("Esame")),
    Rule(
        EventKind.LECTURE,
        _title_starts_with("Lezione: Didattica - "),
        title=NAME_PLACEHOLDER,
    ),
    Rule(EventKind.LECTURE, _category("Lezione")),
    Rule(EventKind.DEADLINE, _title_starts_with("Scadenza: ")),
    Rule(EventKind.DEADLINE, _category("Scadenza")),
)

POLIMI_ENROLLMENT = EnrollmentRules(
    enrolled=Condition(Field.DESCRIPTION, MatchKind.STARTS_WITH, "Iscritto"),
    not_enrolled=Condition(Field.DESCRIPTION, MatchKind.STARTS_WITH, "Non iscritto"),
)
