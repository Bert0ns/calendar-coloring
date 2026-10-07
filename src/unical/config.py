from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from unical.profile import CalendarSettings


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Relative paths are resolved from the CWD.

    Everything about the calendar itself lives in the profile; the calendar
    names here only override it (e.g. from GitHub Actions secrets).
    """

    source_calendar_name: str | None = None
    target_calendar_name: str | None = None
    source_ical_url: str | None = None
    """When set, events are read from this iCal feed instead of a Google calendar."""
    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    legacy_token_path: Path = Path("token.pickle")
    profile_path: Path = Path("profile.json")

    def calendars(self, saved: CalendarSettings) -> CalendarSettings:
        """The calendars to sync: the profile's, unless overridden."""
        return CalendarSettings(
            source=self.source_calendar_name or saved.source,
            target=self.target_calendar_name or saved.target,
            time_zone=saved.time_zone,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        def get(key: str, default: str = "") -> str:
            value = env.get(key, "").strip()
            return value or default

        defaults = cls()
        return cls(
            source_calendar_name=get("SOURCE_CALENDAR_NAME") or None,
            target_calendar_name=get("TARGET_CALENDAR_NAME") or None,
            source_ical_url=get("SOURCE_ICAL_URL") or None,
            credentials_path=Path(
                get("CREDENTIALS_PATH", str(defaults.credentials_path))
            ),
            token_path=Path(get("TOKEN_PATH", str(defaults.token_path))),
            legacy_token_path=Path(
                get("LEGACY_TOKEN_PATH", str(defaults.legacy_token_path))
            ),
            profile_path=Path(get("PROFILE_PATH", str(defaults.profile_path))),
        )
