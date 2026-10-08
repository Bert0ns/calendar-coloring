import json
from pathlib import Path
from typing import Any

import pytest

from unical.custom_events import CustomEvent
from unical.events import ExamOccurrence
from unical.palette import GoogleColor
from unical.preferences import ExamPreference, Preferences
from unical.presets import POLIMI_ENROLLMENT, POLIMI_NAME, POLIMI_RULES
from unical.profile import (
    CalendarSettings,
    JsonProfileRepository,
    Profile,
    polimi_profile,
    serialize,
)
from unical.rules import (
    Condition,
    EnrollmentRules,
    EventKind,
    Field,
    MatchKind,
    Rule,
)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "profile.json"


def make_repo(path: Path, warnings: list[str] | None = None) -> JsonProfileRepository:
    sink = warnings.append if warnings is not None else (lambda _: None)
    return JsonProfileRepository(path, on_warning=sink)


def write(path: Path, **sections: Any) -> None:
    data = {"version": 1, "rules": [], **sections}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_missing_file_loads_the_polimi_profile(path: Path) -> None:
    profile = make_repo(path).load()

    assert profile == polimi_profile()


def test_file_is_created_with_polimi_defaults_when_first_saved(path: Path) -> None:
    repo = make_repo(path)
    repo.save(repo.load())

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == POLIMI_NAME
    assert len(data["rules"]) == len(POLIMI_RULES)
    assert data["courses"] == {}
    assert data["exams"] == {}
    assert data["deadlines"] == {}
    assert data["custom_events"] == []


def test_roundtrip(path: Path) -> None:
    profile = Profile(
        name="Università degli Studi",
        calendars=CalendarSettings("Uni", "Mine", "Europe/Rome"),
        rules=[Rule(EventKind.EXAM, Condition(Field.TITLE, MatchKind.EQUALS, "Esame"))],
        enrollment=EnrollmentRules(
            not_enrolled=Condition(Field.DESCRIPTION, MatchKind.CONTAINS, "No")
        ),
        preferences=Preferences(
            course_colors={"Fisica": GoogleColor.PEACOCK},
            exams={
                ExamOccurrence("Analisi 1", "2026-02-01").key: ExamPreference(
                    GoogleColor.FLAMINGO, True
                )
            },
            deadline_colors={"Consegna": GoogleColor.TANGERINE},
        ),
    )

    repo = make_repo(path)
    repo.save(profile)

    assert repo.load() == profile


def test_file_format(path: Path) -> None:
    profile = Profile(
        name="My Uni",
        rules=[
            Rule(
                EventKind.LECTURE,
                Condition(Field.TITLE, MatchKind.REGEX, r"^(?P<name>.+) - L$", True),
                title="{name}",
            )
        ],
        enrollment=EnrollmentRules(
            enrolled=Condition(Field.DESCRIPTION, MatchKind.CONTAINS, "Enrolled")
        ),
        preferences=Preferences(course_colors={"Algebra": GoogleColor.SAGE}),
    )

    make_repo(path).save(profile)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "name": "My Uni",
        "calendars": {
            "source": "Calendar",
            "target": "Calendar Colored",
            "time_zone": None,
        },
        "rules": [
            {
                "kind": "lecture",
                "field": "title",
                "match": "regex",
                "value": r"^(?P<name>.+) - L$",
                "ignore_case": True,
                "title": "{name}",
            }
        ],
        "enrollment": {
            "enrolled": {
                "field": "description",
                "match": "contains",
                "value": "Enrolled",
                "ignore_case": False,
            },
            "not_enrolled": None,
        },
        "courses": {"Algebra": "2"},
        "exams": {},
        "deadlines": {},
        "custom_events": [],
    }


def test_non_ascii_names_are_written_as_is(path: Path) -> None:
    profile = polimi_profile()
    profile.preferences.set_course_color("Analisi Matematica è", GoogleColor.SAGE)

    make_repo(path).save(profile)

    assert "Analisi Matematica è" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["{invalid json", "[1, 2]", '"string"', "\xff\xfe"])
def test_corrupted_file_warns_and_starts_from_polimi(path: Path, content: str) -> None:
    path.write_bytes(content.encode("latin-1"))
    warnings: list[str] = []

    profile = make_repo(path, warnings).load()

    assert profile == polimi_profile()
    assert len(warnings) == 1
    assert str(path) in warnings[0]


def test_missing_rules_and_enrollment_use_the_built_in_ones(path: Path) -> None:
    path.write_text(json.dumps({"calendars": {"source": "Uni"}}), encoding="utf-8")
    warnings: list[str] = []

    profile = make_repo(path, warnings).load()

    assert profile.rules == list(POLIMI_RULES)
    assert profile.enrollment == POLIMI_ENROLLMENT
    assert profile.calendars.source == "Uni"
    assert warnings == []


def test_empty_rules_classify_nothing(path: Path) -> None:
    write(path, enrollment={})

    profile = make_repo(path).load()

    assert profile.rules == []
    assert profile.enrollment == EnrollmentRules()


def test_other_versions_are_read_with_a_warning(path: Path) -> None:
    write(path, version=2, name="Future")
    warnings: list[str] = []

    assert make_repo(path, warnings).load().name == "Future"
    assert "format version 2" in warnings[0]


def test_invalid_rules_are_skipped_with_a_warning(path: Path) -> None:
    good = {"kind": "exam", "field": "title", "match": "starts_with", "value": "E: "}
    write(
        path,
        rules=[
            good,
            {**good, "kind": "seminar"},
            {**good, "field": "organizer"},
            {**good, "match": "fuzzy"},
            {**good, "value": ""},
            {**good, "match": "regex", "value": "(unclosed"},
            "not a rule",
        ],
    )
    warnings: list[str] = []

    profile = make_repo(path, warnings).load()

    assert profile.rules == [
        Rule(EventKind.EXAM, Condition(Field.TITLE, MatchKind.STARTS_WITH, "E: "))
    ]
    assert len(warnings) == 6
    assert warnings[0].startswith("Ignoring rule 2:")
    assert warnings[4].startswith("Ignoring rule 6: invalid regular expression")


def test_rules_must_be_a_list(path: Path) -> None:
    write(path, rules={"kind": "exam"})
    warnings: list[str] = []

    assert make_repo(path, warnings).load().rules == []
    assert warnings == ["Ignoring the rules: they must be a list."]


def test_invalid_enrollment_rule_is_skipped(path: Path) -> None:
    write(
        path,
        enrollment={
            "enrolled": {"field": "description", "match": "nope", "value": "x"},
            "not_enrolled": {
                "field": "description",
                "match": "equals",
                "value": "No",
            },
        },
    )
    warnings: list[str] = []

    profile = make_repo(path, warnings).load()

    assert profile.enrollment == EnrollmentRules(
        not_enrolled=Condition(Field.DESCRIPTION, MatchKind.EQUALS, "No")
    )
    assert len(warnings) == 1
    assert "'enrolled'" in warnings[0]


@pytest.mark.parametrize(
    "section", ["calendars", "enrollment", "courses", "exams", "deadlines"]
)
def test_sections_of_the_wrong_type_are_ignored(path: Path, section: str) -> None:
    write(path, **{section: ["not", "an", "object"]})
    warnings: list[str] = []

    make_repo(path, warnings).load()

    assert warnings == [f"Ignoring '{section}': it must be a JSON object."]


def test_missing_calendar_names_use_the_defaults(path: Path) -> None:
    write(path, calendars={"source": "Uni", "target": ""})

    assert make_repo(path).load().calendars == CalendarSettings(
        source="Uni", target="Calendar Colored"
    )


def test_invalid_preferences_are_skipped_with_a_warning(path: Path) -> None:
    write(
        path,
        courses={"Good": "2", "Bad": "42", "Null": None},
        exams={
            "Good (2027-01-01)": {"color": "8", "subscribed": False},
            "BadColor (2027-01-01)": {"color": "x", "subscribed": True},
            "NotADict (2027-01-01)": "11",
            "NoSub (2027-01-01)": {"color": "11"},
        },
        deadlines={"Tesi": "5", "Bad": "0"},
    )
    warnings: list[str] = []

    prefs = make_repo(path, warnings).load().preferences

    assert prefs.course_colors == {"Good": GoogleColor.SAGE}
    assert prefs.exams == {
        "Good (2027-01-01)": ExamPreference(GoogleColor.GRAPHITE, False),
        "NoSub (2027-01-01)": ExamPreference(GoogleColor.TOMATO, False),
    }
    assert prefs.deadline_colors == {"Tesi": GoogleColor.BANANA}
    assert len(warnings) == 5
    assert "Ignoring invalid color '0' for deadline 'Bad'." in warnings


def test_corrupted_file_is_rewritten_only_on_save(path: Path) -> None:
    path.write_text("{invalid json")
    repo = make_repo(path)
    profile = repo.load()
    assert path.read_text() == "{invalid json"

    repo.save(profile)

    assert make_repo(path).load() == polimi_profile()


def test_save_skips_unchanged_profile(path: Path) -> None:
    data = json.dumps(serialize(polimi_profile()), separators=(",", ":"))
    path.write_text(data)  # non-canonical formatting on purpose
    repo = make_repo(path)

    repo.save(repo.load())

    assert path.read_text() == data


def test_save_writes_again_after_further_changes(path: Path) -> None:
    repo = make_repo(path)
    profile = repo.load()
    profile.preferences.set_course_color("A", GoogleColor.SAGE)
    repo.save(profile)
    profile.preferences.set_course_color("A", GoogleColor.GRAPE)
    repo.save(profile)

    assert json.loads(path.read_text())["courses"] == {"A": "3"}


def test_save_creates_parent_directories_and_leaves_no_temp_files(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "nested" / "profile.json")

    repo.save(repo.load())

    assert [p.name for p in (tmp_path / "nested").iterdir()] == ["profile.json"]


def test_failed_write_keeps_previous_file_intact(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(path, courses={"A": "1"})
    original = path.read_text()
    repo = make_repo(path)
    profile = repo.load()
    profile.preferences.set_course_color("B", GoogleColor.SAGE)

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("unical.profile.json.dump", explode)
    with pytest.raises(OSError):
        repo.save(profile)

    assert path.read_text() == original
    assert [p.name for p in path.parent.iterdir()] == ["profile.json"]


def test_classifier_uses_the_profile_rules() -> None:
    profile = polimi_profile()
    assert profile.classifier.course_name({"summary": "Lezione: Didattica - CS"}) == (
        "CS"
    )
    profile.rules = []
    assert profile.classifier.classify({"summary": "Lezione: Didattica - CS"}) is None


def test_time_zone(path: Path) -> None:
    write(path, calendars={"time_zone": "America/New_York"})
    assert make_repo(path).load().calendars.time_zone == "America/New_York"

    write(path, calendars={"time_zone": "Mars/Olympus_Mons"})
    warnings: list[str] = []
    assert make_repo(path, warnings).load().calendars.time_zone is None
    assert warnings == ["Ignoring unknown time zone 'Mars/Olympus_Mons'."]


def test_custom_events_roundtrip(path: Path) -> None:
    cst = CustomEvent(
        id="cst12345",
        summary="Study Session",
        start={"dateTime": "2026-10-15T10:00:00+02:00"},
        end={"dateTime": "2026-10-15T12:00:00+02:00"},
        description="Review chapter 4",
        location="Room 101",
        color_id="5",
        recurrence=("RRULE:FREQ=WEEKLY;BYDAY=TH",),
        source="created",
    )
    profile = polimi_profile()
    profile.custom_events = [cst]

    repo = make_repo(path)
    repo.save(profile)
    loaded = repo.load()

    assert len(loaded.custom_events) == 1
    assert loaded.custom_events[0] == cst


def test_invalid_custom_events_are_skipped(path: Path) -> None:
    write(
        path,
        custom_events=[
            "not a dict",
            {"summary": "No ID", "start": {}, "end": {}},
            {"id": "1", "start": {}, "end": {}},
            {"id": "2", "summary": "No start/end"},
            {
                "id": "cst_valid",
                "summary": "Valid Event",
                "start": {"date": "2026-10-15"},
                "end": {"date": "2026-10-15"},
                "recurrence": ["RRULE:FREQ=DAILY"],
            },
        ],
    )
    warnings: list[str] = []
    profile = make_repo(path, warnings).load()

    assert len(profile.custom_events) == 1
    assert profile.custom_events[0].id == "cst_valid"
    assert len(warnings) == 4


def test_custom_events_must_be_a_list(path: Path) -> None:
    write(path, custom_events={"id": "bad"})
    warnings: list[str] = []

    profile = make_repo(path, warnings).load()
    assert profile.custom_events == []
    assert warnings == ["Ignoring 'custom_events': it must be a list."]
