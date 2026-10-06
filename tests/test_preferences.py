import json
from pathlib import Path

import pytest

from calendar_coloring.events import ExamOccurrence
from calendar_coloring.palette import GoogleColor
from calendar_coloring.preferences import (
    ExamPreference,
    JsonPreferencesRepository,
    Preferences,
)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "course_colors.json",
        tmp_path / "exam_states.json",
        tmp_path / "deadline_colors.json",
    )


def make_repo(paths: tuple[Path, Path, Path], warnings: list[str] | None = None):
    sink = warnings.append if warnings is not None else (lambda _: None)
    return JsonPreferencesRepository(*paths, on_warning=sink)


def test_missing_files_load_as_empty(paths) -> None:
    prefs = make_repo(paths).load()
    assert prefs == Preferences()


def test_loads_legacy_format(paths) -> None:
    courses, exams, _ = paths
    courses.write_text(json.dumps({"Computer Security": "4"}))
    exams.write_text(
        json.dumps({"Esame: Security (2026-06-15)": {"color": "3", "subscribed": True}})
    )

    prefs = make_repo(paths).load()

    assert prefs.course_colors == {"Computer Security": GoogleColor.FLAMINGO}
    assert prefs.exams == {
        "Esame: Security (2026-06-15)": ExamPreference(GoogleColor.GRAPE, True)
    }


@pytest.mark.parametrize("content", ["{invalid json", "[1, 2]", '"string"', "\xff\xfe"])
def test_corrupted_file_warns_and_starts_fresh(paths, content: str) -> None:
    courses, _, _ = paths
    courses.write_bytes(content.encode("latin-1"))
    warnings: list[str] = []

    prefs = make_repo(paths, warnings).load()

    assert prefs.course_colors == {}
    assert len(warnings) == 1
    assert str(courses) in warnings[0]


def test_corrupted_file_is_not_overwritten_if_untouched(paths) -> None:
    courses, _, _ = paths
    courses.write_text("{invalid json")
    repo = make_repo(paths)
    prefs = repo.load()
    prefs.set_exam(
        ExamOccurrence("Esame: X", "2027-01-01"),
        ExamPreference(GoogleColor.SAGE, False),
    )

    repo.save(prefs)

    assert courses.read_text() == "{invalid json"


def test_invalid_entries_are_skipped_with_a_warning(paths) -> None:
    courses, exams, _ = paths
    courses.write_text(json.dumps({"Good": "2", "Bad": "42", "Null": None}))
    exams.write_text(
        json.dumps(
            {
                "Esame: Good (2027-01-01)": {"color": "8", "subscribed": False},
                "Esame: BadColor (2027-01-01)": {"color": "x", "subscribed": True},
                "Esame: NotADict (2027-01-01)": "11",
                "Esame: NoSub (2027-01-01)": {"color": "11"},
            }
        )
    )
    warnings: list[str] = []

    prefs = make_repo(paths, warnings).load()

    assert prefs.course_colors == {"Good": GoogleColor.SAGE}
    assert prefs.exams == {
        "Esame: Good (2027-01-01)": ExamPreference(GoogleColor.GRAPHITE, False),
        "Esame: NoSub (2027-01-01)": ExamPreference(GoogleColor.TOMATO, False),
    }
    assert len(warnings) == 4


def test_round_trip_preserves_order_and_format(paths) -> None:
    repo = make_repo(paths)
    prefs = repo.load()
    prefs.set_course_color("Zeta", GoogleColor.BASIL)
    prefs.set_course_color("Alpha", GoogleColor.LAVENDER)
    exam = ExamOccurrence("Esame: X", "2027-01-01")
    prefs.set_exam(exam, ExamPreference(GoogleColor.TOMATO, True))

    repo.save(prefs)

    courses, exams, _ = paths
    assert courses.read_text() == json.dumps({"Zeta": "10", "Alpha": "1"}, indent=4)
    assert exams.read_text() == json.dumps(
        {"Esame: X (2027-01-01)": {"color": "11", "subscribed": True}}, indent=4
    )
    assert make_repo(paths).load() == prefs


def test_save_skips_unchanged_files(paths) -> None:
    courses, exams, _ = paths
    courses.write_text('{"A":"1"}')  # non-canonical formatting on purpose
    repo = make_repo(paths)
    prefs = repo.load()

    repo.save(prefs)

    assert courses.read_text() == '{"A":"1"}'
    assert not exams.exists()


def test_save_writes_again_after_further_changes(paths) -> None:
    repo = make_repo(paths)
    prefs = repo.load()
    prefs.set_course_color("A", GoogleColor.SAGE)
    repo.save(prefs)
    prefs.set_course_color("A", GoogleColor.GRAPE)
    repo.save(prefs)

    assert json.loads(paths[0].read_text()) == {"A": "3"}


def test_save_creates_parent_directories_and_leaves_no_temp_files(tmp_path) -> None:
    paths = tuple(tmp_path / "nested" / f"{n}.json" for n in ("c", "e", "d"))
    repo = make_repo(paths)
    prefs = repo.load()
    prefs.set_course_color("A", GoogleColor.SAGE)

    repo.save(prefs)

    assert [p.name for p in (tmp_path / "nested").iterdir()] == ["c.json"]


def test_failed_write_keeps_previous_file_intact(paths, monkeypatch) -> None:
    courses, _, _ = paths
    courses.write_text(json.dumps({"A": "1"}, indent=4))
    repo = make_repo(paths)
    prefs = repo.load()
    prefs.set_course_color("B", GoogleColor.SAGE)

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("calendar_coloring.preferences.json.dump", explode)
    with pytest.raises(OSError):
        repo.save(prefs)

    assert json.loads(courses.read_text()) == {"A": "1"}
    assert sorted(p.name for p in courses.parent.iterdir()) == ["course_colors.json"]


def test_subscribed_exam_titles() -> None:
    prefs = Preferences(
        exams={
            "Esame: A (2027-01-01)": ExamPreference(GoogleColor.TOMATO, True),
            "Esame: A (2027-02-01)": ExamPreference(GoogleColor.GRAPHITE, False),
            "Esame: B (2027-01-01)": ExamPreference(GoogleColor.GRAPHITE, False),
            "Esame: C (Prova) (2027-01-01)": ExamPreference(GoogleColor.TOMATO, True),
        }
    )
    assert prefs.subscribed_exam_titles() == {"Esame: A", "Esame: C (Prova)"}


def test_deadline_colors_round_trip(paths) -> None:
    _, _, deadlines = paths
    deadlines.write_text(json.dumps({"Esame di laurea": "5", "Bad": "0"}))
    warnings: list[str] = []
    repo = make_repo(paths, warnings)

    prefs = repo.load()
    assert prefs.deadline_colors == {"Esame di laurea": GoogleColor.BANANA}
    assert warnings == ["Ignoring invalid color '0' for deadline 'Bad'."]

    prefs.set_deadline_color("Consegna tesi", GoogleColor.SAGE)
    repo.save(prefs)
    assert json.loads(deadlines.read_text()) == {
        "Esame di laurea": "5",
        "Consegna tesi": "2",
    }


def test_empty_preferences_create_no_files(paths) -> None:
    repo = make_repo(paths)
    repo.save(repo.load())
    assert not any(path.exists() for path in paths)
