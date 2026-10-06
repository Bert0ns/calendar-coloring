from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SOURCE_CALENDAR = "Polimi Calendar"
DEFAULT_TARGET_CALENDAR = "Polimi Calendar Colored"


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Relative paths are resolved from the CWD."""

    source_calendar_name: str = DEFAULT_SOURCE_CALENDAR
    target_calendar_name: str = DEFAULT_TARGET_CALENDAR
    source_ical_url: str | None = None
    """When set, events are read from this iCal feed instead of a Google calendar."""
    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    legacy_token_path: Path = Path("token.pickle")
    course_colors_path: Path = Path("course_colors.json")
    exam_states_path: Path = Path("exam_states.json")
    deadline_colors_path: Path = Path("deadline_colors.json")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        def get(key: str, default: str) -> str:
            value = env.get(key, "").strip()
            return value or default

        defaults = cls()
        return cls(
            source_calendar_name=get(
                "SOURCE_CALENDAR_NAME", defaults.source_calendar_name
            ),
            target_calendar_name=get(
                "TARGET_CALENDAR_NAME", defaults.target_calendar_name
            ),
            source_ical_url=get("SOURCE_ICAL_URL", "") or None,
            credentials_path=Path(
                get("CREDENTIALS_PATH", str(defaults.credentials_path))
            ),
            token_path=Path(get("TOKEN_PATH", str(defaults.token_path))),
            legacy_token_path=Path(
                get("LEGACY_TOKEN_PATH", str(defaults.legacy_token_path))
            ),
            course_colors_path=Path(
                get("COURSE_COLORS_PATH", str(defaults.course_colors_path))
            ),
            exam_states_path=Path(
                get("EXAM_STATES_PATH", str(defaults.exam_states_path))
            ),
            deadline_colors_path=Path(
                get("DEADLINE_COLORS_PATH", str(defaults.deadline_colors_path))
            ),
        )
