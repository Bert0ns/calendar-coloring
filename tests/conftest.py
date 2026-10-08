from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from unical.config import Config
from unical.presets import POLIMI_ENROLLMENT, POLIMI_RULES
from unical.profile import JsonProfileRepository, polimi_profile, serialize
from unical.rules import Classifier
from unical.sync.models import (
    CalendarInfo,
    Mutation,
    MutationAction,
    MutationResult,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def as_stored_by_google(event: dict[str, Any]) -> dict[str, Any]:
    """Google omits empty fields (e.g. ``description: ""``) from responses."""
    return {k: copy.deepcopy(v) for k, v in event.items() if v != ""}


class FakeCalendarGateway:
    """In-memory calendar backend that really applies mutations."""

    def __init__(self, calendars: dict[str, list[dict[str, Any]]] | None = None):
        # name -> events; IDs are derived from names.
        self.calendars: dict[str, dict[str, Any]] = {}
        for name, events in (calendars or {}).items():
            self.add_calendar(name, events)
        self.created: list[str] = []
        self.batches: list[tuple[str, list[Mutation]]] = []
        self.fail_event_ids: set[str] = set()
        self.listings: list[tuple[str, bool]] = []
        self.time_zones: dict[str, str | None] = {}
        """Time zone each calendar was created with."""
        self.read_only: set[str] = set()
        """Names of the calendars the user can only read."""

    def add_calendar(self, name: str, events: list[dict[str, Any]]) -> str:
        cal_id = f"id::{name}"
        self.calendars[cal_id] = {"name": name, "events": copy.deepcopy(events)}
        return cal_id

    def events_of(self, name: str) -> list[dict[str, Any]]:
        return self.calendars[f"id::{name}"]["events"]

    # -- CalendarGateway -----------------------------------------------------

    def list_calendars(self) -> list[CalendarInfo]:
        return [
            CalendarInfo(
                id=cal_id, name=cal["name"], writable=cal["name"] not in self.read_only
            )
            for cal_id, cal in self.calendars.items()
        ]

    def get_calendar_id_by_name(self, name: str) -> str | None:
        for cal_id, cal in self.calendars.items():
            if cal["name"] == name:
                return cal_id
        return None

    def create_calendar(self, name: str, time_zone: str | None = None) -> str:
        self.created.append(name)
        self.time_zones[name] = time_zone
        return self.add_calendar(name, [])

    def get_all_events(
        self,
        calendar_id: str,
        expand_recurring: bool = True,
        time_min: datetime | date | None = None,
        time_max: datetime | date | None = None,
    ) -> list[dict[str, Any]]:
        self.listings.append((calendar_id, expand_recurring))
        events = copy.deepcopy(self.calendars[calendar_id]["events"])
        if time_min is not None or time_max is not None:
            filtered = []
            for ev in events:
                start = ev.get("start") or {}
                raw = start.get("dateTime", start.get("date"))
                if not raw:
                    filtered.append(ev)
                    continue
                try:
                    ev_d = date.fromisoformat(str(raw)[:10])
                    if time_min is not None:
                        t_min = (
                            time_min.date()
                            if isinstance(time_min, datetime)
                            else time_min
                        )
                        if ev_d < t_min:
                            continue
                    if time_max is not None:
                        t_max = (
                            time_max.date()
                            if isinstance(time_max, datetime)
                            else time_max
                        )
                        if ev_d > t_max:
                            continue
                except ValueError:
                    pass
                filtered.append(ev)
            return filtered
        return events

    def batch_mutate_events(
        self, calendar_id: str, mutations: Sequence[Mutation]
    ) -> list[MutationResult]:
        self.batches.append((calendar_id, list(mutations)))
        events = self.calendars[calendar_id]["events"]
        results = []
        for m in mutations:
            if m.event_id in self.fail_event_ids:
                results.append(MutationResult(m, RuntimeError("boom")))
                continue
            if m.action is MutationAction.INSERT:
                events.append(as_stored_by_google(m.body))
            elif m.action is MutationAction.UPDATE:
                index = next(i for i, e in enumerate(events) if e["id"] == m.event_id)
                events[index] = as_stored_by_google(m.body)
            else:
                events[:] = [e for e in events if e.get("id") != m.event_id]
            results.append(MutationResult(m))
        return results

    @property
    def all_mutations(self) -> list[Mutation]:
        return [m for _, batch in self.batches for m in batch]


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temp dir so nothing touches the repo's real state
    files (course_colors.json, exam_states.json, ...)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "user_config" / "unical"
    monkeypatch.setattr("unical.config.default_config_dir", lambda: config_dir)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        source_calendar_name="Src",
        target_calendar_name="Tgt",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        legacy_token_path=tmp_path / "token.pickle",
        profile_path=tmp_path / "profile.json",
    )


POLIMI = Classifier(POLIMI_RULES, POLIMI_ENROLLMENT)
"""Classifier of the built-in PoliMi profile."""


def saved(config: Config, section: str) -> Any:
    """A section of the saved profile (``{}`` if there is no profile file)."""
    if not config.profile_path.exists():
        return {}
    return json.loads(config.profile_path.read_text(encoding="utf-8"))[section]


def save_profile(config: Config, **sections: Any) -> None:
    """Writes the PoliMi profile with the given sections (e.g. ``courses``)."""
    data = serialize(polimi_profile())
    data.update(sections)
    config.profile_path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def profile_repository(config: Config) -> JsonProfileRepository:
    return JsonProfileRepository(config.profile_path)
