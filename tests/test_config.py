from pathlib import Path

from polimi_calendar_coloring.config import Config


def test_defaults_are_backwards_compatible() -> None:
    config = Config.from_env({})
    assert config == Config()
    assert config.source_calendar_name == "Polimi Calendar"
    assert config.target_calendar_name == "Polimi Calendar Colored"
    assert config.credentials_path == Path("credentials.json")
    assert config.token_path == Path("token.json")
    assert config.legacy_token_path == Path("token.pickle")
    assert config.course_colors_path == Path("course_colors.json")
    assert config.exam_states_path == Path("exam_states.json")
    assert config.deadline_colors_path == Path("deadline_colors.json")
    assert config.source_ical_url is None


def test_reads_every_setting_from_env() -> None:
    config = Config.from_env(
        {
            "SOURCE_CALENDAR_NAME": "Src",
            "TARGET_CALENDAR_NAME": "Tgt",
            "CREDENTIALS_PATH": "/a/c.json",
            "TOKEN_PATH": "/a/t.json",
            "LEGACY_TOKEN_PATH": "/a/t.pickle",
            "COURSE_COLORS_PATH": "/a/cc.json",
            "EXAM_STATES_PATH": "/a/es.json",
            "DEADLINE_COLORS_PATH": "/a/dc.json",
            "SOURCE_ICAL_URL": "https://ical/1/x",
        }
    )
    assert config == Config(
        source_calendar_name="Src",
        target_calendar_name="Tgt",
        source_ical_url="https://ical/1/x",
        credentials_path=Path("/a/c.json"),
        token_path=Path("/a/t.json"),
        legacy_token_path=Path("/a/t.pickle"),
        course_colors_path=Path("/a/cc.json"),
        exam_states_path=Path("/a/es.json"),
        deadline_colors_path=Path("/a/dc.json"),
    )


def test_blank_values_fall_back_to_defaults() -> None:
    # GitHub Actions passes unset secrets as empty strings.
    config = Config.from_env(
        {"SOURCE_CALENDAR_NAME": "", "TOKEN_PATH": "   ", "SOURCE_ICAL_URL": ""}
    )
    assert config == Config()


def test_values_are_stripped() -> None:
    assert (
        Config.from_env({"TARGET_CALENDAR_NAME": " Tgt "}).target_calendar_name == "Tgt"
    )
