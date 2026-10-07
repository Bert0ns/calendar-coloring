from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

from unical.profile import CalendarSettings

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
LEGACY_TOKEN_FILE = "token.pickle"
PROFILE_FILE = "profile.json"


def default_config_dir() -> Path:
    """Standard platform-specific user configuration directory for unical."""
    return platformdirs.user_config_path("unical", appauthor=False)


def resolve_config_path(
    env_value: str | None,
    filename: str,
    *,
    cwd: Path | None = None,
    config_dir: Path | None = None,
) -> Path:
    """Resolve a configuration file path according to the resolution order:

    1. Explicit environment variable (if non-empty).
    2. Current working directory if the file exists.
    3. Platform standard user config directory.
    """
    if env_value:
        return Path(env_value)
    target_cwd = cwd if cwd is not None else Path.cwd()
    candidate = target_cwd / filename
    if candidate.is_file():
        return Path(filename) if cwd is None else candidate
    base_dir = config_dir if config_dir is not None else default_config_dir()
    return base_dir / filename


@dataclass(frozen=True)
class Config:
    """Runtime configuration.

    Configuration paths (credentials, token, profile) are resolved in order:
    1. Explicit environment variables (CREDENTIALS_PATH, TOKEN_PATH, etc.).
    2. Current working directory if the file exists.
    3. Platform standard user config directory (e.g. ~/.config/unical on Linux).

    Everything about the calendar itself lives in the profile; the calendar
    names here only override it (e.g. from GitHub Actions secrets).
    """

    source_calendar_name: str | None = None
    target_calendar_name: str | None = None
    source_ical_url: str | None = None
    """When set, events are read from this iCal feed instead of a Google calendar."""
    credentials_path: Path = field(
        default_factory=lambda: resolve_config_path(None, CREDENTIALS_FILE)
    )
    token_path: Path = field(
        default_factory=lambda: resolve_config_path(None, TOKEN_FILE)
    )
    legacy_token_path: Path = field(
        default_factory=lambda: resolve_config_path(None, LEGACY_TOKEN_FILE)
    )
    profile_path: Path = field(
        default_factory=lambda: resolve_config_path(None, PROFILE_FILE)
    )

    def calendars(self, saved: CalendarSettings) -> CalendarSettings:
        """The calendars to sync: the profile's, unless overridden."""
        return CalendarSettings(
            source=self.source_calendar_name or saved.source,
            target=self.target_calendar_name or saved.target,
            time_zone=saved.time_zone,
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        cwd: Path | None = None,
        config_dir: Path | None = None,
    ) -> Config:
        def get(key: str) -> str | None:
            value = env.get(key, "").strip()
            return value or None

        return cls(
            source_calendar_name=get("SOURCE_CALENDAR_NAME"),
            target_calendar_name=get("TARGET_CALENDAR_NAME"),
            source_ical_url=get("SOURCE_ICAL_URL"),
            credentials_path=resolve_config_path(
                get("CREDENTIALS_PATH"),
                CREDENTIALS_FILE,
                cwd=cwd,
                config_dir=config_dir,
            ),
            token_path=resolve_config_path(
                get("TOKEN_PATH"),
                TOKEN_FILE,
                cwd=cwd,
                config_dir=config_dir,
            ),
            legacy_token_path=resolve_config_path(
                get("LEGACY_TOKEN_PATH"),
                LEGACY_TOKEN_FILE,
                cwd=cwd,
                config_dir=config_dir,
            ),
            profile_path=resolve_config_path(
                get("PROFILE_PATH"),
                PROFILE_FILE,
                cwd=cwd,
                config_dir=config_dir,
            ),
        )
