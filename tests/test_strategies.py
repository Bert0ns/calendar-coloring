import json
from unittest.mock import patch

from strategies.base import CompositeColoringStrategy, PersistentColoringStrategy
from strategies.exams import ExamColoringStrategy
from strategies.lectures import LectureColoringStrategy


class DummyStrategy(PersistentColoringStrategy):
    def determine_color(self, event):
        return self.state.get(event.get("id"))


def test_persistent_strategy_corrupted_json(tmp_path):
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{invalid json")

    strategy = DummyStrategy(str(corrupt_file))
    assert strategy.state == {}


def test_composite_strategy():
    class S1(PersistentColoringStrategy):
        def determine_color(self, event):
            if event.get("type") == "a":
                return "1"
            return None

    class S2(PersistentColoringStrategy):
        def determine_color(self, event):
            if event.get("type") == "b":
                return "2"
            return None

    composite = CompositeColoringStrategy([S1("dummy1"), S2("dummy2")])
    assert composite.determine_color({"type": "a"}) == "1"
    assert composite.determine_color({"type": "b"}) == "2"
    assert composite.determine_color({"type": "c"}) is None


def test_exam_strategy_ignores_non_exams(tmp_path):
    state_file = tmp_path / "exams.json"
    strategy = ExamColoringStrategy()
    strategy.state_file = str(state_file)
    strategy.state = {}

    event = {"summary": "Lezione: Didattica - Algoritmi"}
    assert strategy.determine_color(event) is None


def test_exam_strategy_auto_colors_and_declines(tmp_path):
    state_file = tmp_path / "exams.json"
    strategy = ExamColoringStrategy()
    strategy.state_file = str(state_file)
    strategy.state = {}
    strategy.subscribed_titles = set()

    # First session: Subscribed
    event_sub = {
        "summary": "Esame: Security",
        "description": "Iscritto all'appello",
        "start": {"date": "2026-06-15"},
    }
    color1 = strategy.determine_color(event_sub)
    assert color1 == ExamColoringStrategy.COLOR_RED
    assert "Esame: Security" in strategy.subscribed_titles

    # Second session for same exam: Auto-declines (Grey)
    event_dup = {
        "summary": "Esame: Security",
        "description": "Non iscritto all'appello",
        "start": {"date": "2026-07-15"},
    }
    color2 = strategy.determine_color(event_dup)
    assert color2 == ExamColoringStrategy.COLOR_GREY


def test_exam_strategy_interactive_defaults(tmp_path):
    state_file = tmp_path / "exams.json"
    state_file.write_text(
        json.dumps({"Esame: Security (2026-06-15)": {"color": "3", "subscribed": True}})
    )

    strategy = ExamColoringStrategy(interactive=True)
    strategy.state_file = str(state_file)
    strategy.state = strategy._load_state()
    strategy.subscribed_titles = strategy._derive_subscribed_titles()

    event = {
        "summary": "Esame: Security",
        "description": "Iscritto",
        "start": {"date": "2026-06-15"},
    }

    # Simulate pressing Enter twice (accepting defaults: "y" and color "3")
    with patch("builtins.input", side_effect=["", ""]):
        color = strategy.determine_color(event)

    assert color == "3"
    assert strategy.state["Esame: Security (2026-06-15)"]["subscribed"] is True


def test_lecture_strategy_deterministic_coloring(tmp_path):
    state_file = tmp_path / "courses.json"
    strategy = LectureColoringStrategy()
    strategy.state_file = str(state_file)
    strategy.state = {}

    course_a = "Lezione: Didattica - Computer Security"
    course_b = "Lezione: Didattica - Machine Learning"

    color_a1 = strategy.determine_color({"summary": course_a})
    color_b1 = strategy.determine_color({"summary": course_b})

    # Create fresh strategy and process in reverse order
    strategy_reverse = LectureColoringStrategy()
    strategy_reverse.state_file = str(tmp_path / "courses2.json")
    strategy_reverse.state = {}

    color_b2 = strategy_reverse.determine_color({"summary": course_b})
    color_a2 = strategy_reverse.determine_color({"summary": course_a})

    # Order must not affect deterministic colors
    assert color_a1 == color_a2
    assert color_b1 == color_b2
    assert color_a1 in LectureColoringStrategy.AVAILABLE_LECTURE_COLORS


def test_lecture_strategy_interactive_defaults(tmp_path):
    state_file = tmp_path / "courses.json"
    state_file.write_text(json.dumps({"Computer Security": "4"}))

    strategy = LectureColoringStrategy(interactive=True)
    strategy.state_file = str(state_file)
    strategy.state = strategy._load_state()

    event = {"summary": "Lezione: Didattica - Computer Security"}

    # Simulate pressing Enter to keep existing default color "4"
    with patch("builtins.input", return_value=""):
        color = strategy.determine_color(event)

    assert color == "4"
    assert strategy.state["Computer Security"] == "4"


def test_deadline_strategy_deterministic_coloring():
    from strategies.deadlines import DeadlineColoringStrategy

    strategy = DeadlineColoringStrategy()
    strategy.state_file = "deadlines.json"
    strategy.state = {}

    color = strategy.determine_color({"summary": "Scadenza: Esame di laurea"})
    assert color in DeadlineColoringStrategy.AVAILABLE_DEADLINE_COLORS

    # Non-deadline events are ignored
    assert strategy.determine_color({"summary": "Lezione: Didattica - X"}) is None
    assert strategy.determine_color({"summary": "Esame: Something"}) is None


def test_deadline_strategy_interactive_defaults(tmp_path):
    from strategies.deadlines import DeadlineColoringStrategy

    state_file = tmp_path / "deadlines.json"
    state_file.write_text(json.dumps({"Esame di laurea": "5"}))

    strategy = DeadlineColoringStrategy(interactive=True)
    strategy.state_file = str(state_file)
    strategy.state = strategy._load_state()

    event = {"summary": "Scadenza: Esame di laurea"}

    # Simulate pressing Enter to keep existing default color "5"
    with patch("builtins.input", return_value=""):
        color = strategy.determine_color(event)

    assert color == "5"
    assert strategy.state["Esame di laurea"] == "5"


def test_strategies_match_ical_categories_without_prefix():
    from strategies.deadlines import DeadlineColoringStrategy

    exam_strategy = ExamColoringStrategy()
    exam_strategy.state_file = "exams_cat.json"
    exam_strategy.state = {}
    exam_strategy.subscribed_titles = set()

    # Exam recognized via CATEGORIES even without the "Esame: " prefix
    color = exam_strategy.determine_color(
        {
            "summary": "DESIGN AND IMPLEMENTATION OF MOBILE APPLICATIONS",
            "description": "Non iscritto",
            "start": {"date": "2026-09-08"},
            "categories": ["Esame"],
        }
    )
    assert color == ExamColoringStrategy.COLOR_GREY

    lecture_strategy = LectureColoringStrategy()
    lecture_strategy.state_file = "courses_cat.json"
    lecture_strategy.state = {}
    color = lecture_strategy.determine_color(
        {"summary": "SENSOR SYSTEMS", "categories": ["Lezione"]}
    )
    assert color in LectureColoringStrategy.AVAILABLE_LECTURE_COLORS

    deadline_strategy = DeadlineColoringStrategy()
    deadline_strategy.state_file = "deadlines_cat.json"
    deadline_strategy.state = {}
    color = deadline_strategy.determine_color(
        {"summary": "Esame di laurea", "categories": ["Scadenza"]}
    )
    assert color in DeadlineColoringStrategy.AVAILABLE_DEADLINE_COLORS


def test_exam_strategy_interactive_asks_once_per_title():
    strategy = ExamColoringStrategy(interactive=True)
    strategy.state_file = "exams_memo.json"
    strategy.state = {}
    strategy.subscribed_titles = set()

    first = {
        "summary": "Esame: Security",
        "description": "Iscritto",
        "start": {"date": "2026-06-15"},
    }
    second = {
        "summary": "Esame: Security",
        "description": "Non iscritto",
        "start": {"date": "2026-07-15"},
    }

    # First date: two prompts (subscription + color). Second date: reused, no prompts.
    with patch("builtins.input", side_effect=["y", ""]) as mock_input:
        color1 = strategy.determine_color(first)
        color2 = strategy.determine_color(second)

    assert mock_input.call_count == 2
    assert color1 == color2 == ExamColoringStrategy.COLOR_RED
    assert strategy.state["Esame: Security (2026-07-15)"]["subscribed"] is True
