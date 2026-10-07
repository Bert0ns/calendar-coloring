from pathlib import Path

from calendar_coloring.config import Config
from calendar_coloring.profile import CalendarSettings


def test_defaults() -> None:
    config = Config.from_env({})
    assert config == Config()
    assert config.source_calendar_name is None
    assert config.target_calendar_name is None
    assert config.credentials_path == Path("credentials.json")
    assert config.token_path == Path("token.json")
    assert config.legacy_token_path == Path("token.pickle")
    assert config.profile_path == Path("profile.json")
    assert config.source_ical_url is None


def test_reads_every_setting_from_env() -> None:
    config = Config.from_env(
        {
            "SOURCE_CALENDAR_NAME": "Src",
            "TARGET_CALENDAR_NAME": "Tgt",
            "CREDENTIALS_PATH": "/a/c.json",
            "TOKEN_PATH": "/a/t.json",
            "LEGACY_TOKEN_PATH": "/a/t.pickle",
            "PROFILE_PATH": "/a/p.json",
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
        profile_path=Path("/a/p.json"),
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


def test_calendar_names_override_the_profile() -> None:
    saved = CalendarSettings(source="Uni", target="Uni colored")
    assert Config().calendars(saved) == saved
    assert Config(target_calendar_name="Mine").calendars(saved) == CalendarSettings(
        source="Uni", target="Mine"
    )
    assert Config(source_calendar_name="Other").calendars(saved) == (
        CalendarSettings(source="Other", target="Uni colored")
    )


def test_overrides_keep_the_saved_time_zone() -> None:
    saved = CalendarSettings(source="Uni", target="Mine", time_zone="Asia/Tokyo")
    assert Config(target_calendar_name="Other").calendars(saved).time_zone == (
        "Asia/Tokyo"
    )
