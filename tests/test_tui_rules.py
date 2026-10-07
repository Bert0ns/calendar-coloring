import pytest
from conftest import POLIMI

from unical.rules import (
    Classifier,
    Condition,
    EventKind,
    Field,
    MatchKind,
    Rule,
)
from unical.tui.rules import (
    RuleForm,
    TitlePreview,
    count_matches,
    describe,
    kind_counts,
    preview,
    suggest_prefix,
    summary,
)

EVENTS = [
    {"summary": "Lezione: Didattica - CS"},
    {"summary": "Esame: CS", "description": "Iscritto"},
    {"summary": "Lezione: Didattica - CS"},
    {"summary": "Seminario: AI"},
    {"summary": "Scadenza: Tesi"},
    {"summary": "Lezione: Didattica - Math"},
]


@pytest.mark.parametrize(
    ("condition", "text"),
    [
        (
            Condition(Field.TITLE, MatchKind.STARTS_WITH, "Esame: "),
            "title starts with 'Esame: '",
        ),
        (
            Condition(Field.CATEGORY, MatchKind.EQUALS, "Lezione"),
            "a category is 'Lezione'",
        ),
        (
            Condition(Field.DESCRIPTION, MatchKind.CONTAINS, "lab", ignore_case=True),
            "description contains 'lab' (ignore case)",
        ),
        (
            Condition(Field.LOCATION, MatchKind.REGEX, r"^Aula \d"),
            "location matches '^Aula \\\\d'",
        ),
    ],
)
def test_describe(condition: Condition, text: str) -> None:
    assert describe(condition) == text


def test_preview_groups_titles_in_order() -> None:
    assert preview(EVENTS, POLIMI) == [
        TitlePreview("Lezione: Didattica - CS", 2, EventKind.LECTURE, "CS", "CS"),
        TitlePreview("Esame: CS", 1, EventKind.EXAM, "CS", "Esame: CS"),
        TitlePreview("Seminario: AI", 1, None, "", "Seminario: AI"),
        TitlePreview("Scadenza: Tesi", 1, EventKind.DEADLINE, "Tesi", "Scadenza: Tesi"),
        TitlePreview("Lezione: Didattica - Math", 1, EventKind.LECTURE, "Math", "Math"),
    ]


def test_counts_and_summary() -> None:
    previews = preview(EVENTS, POLIMI)
    assert kind_counts(previews) == {
        EventKind.EXAM: 1,
        EventKind.LECTURE: 3,
        EventKind.DEADLINE: 1,
        None: 1,
    }
    assert summary(previews) == "3 lectures · 1 exams · 1 deadlines · 1 unmatched"
    assert summary(preview([], Classifier())) == (
        "0 lectures · 0 exams · 0 deadlines · 0 unmatched"
    )


def test_count_matches() -> None:
    condition = Condition(Field.TITLE, MatchKind.CONTAINS, "CS")
    assert count_matches(condition, EVENTS) == 3


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Lezione: Didattica - CS", "Lezione: Didattica - "),
        ("Esame: CS", "Esame: "),
        ("Seminario: AI", "Seminario: "),
        ("[CS101] Algorithms", "[CS101] "),
        ("Meeting", "Meeting"),
    ],
)
def test_suggest_prefix(title: str, expected: str) -> None:
    titles = [
        "Lezione: Didattica - CS",
        "Lezione: Didattica - Math",
        "Esame: CS - Appello 1",
        "Esame: Computer Security - Appello 2",
        "Esame: CS",
        "Seminario: AI",
        "[CS101] Algorithms",
        "Meeting",
    ]
    assert suggest_prefix(title, titles) == expected


def test_rule_form_round_trip() -> None:
    rule = Rule(
        EventKind.EXAM,
        Condition(Field.DESCRIPTION, MatchKind.REGEX, "x", ignore_case=True),
        title="{name}",
    )
    assert RuleForm.of(rule) == RuleForm(
        EventKind.EXAM, Field.DESCRIPTION, MatchKind.REGEX, "x", True, "{name}"
    )
    assert RuleForm.of_condition(None) == RuleForm()
