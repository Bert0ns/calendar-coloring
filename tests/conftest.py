from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from calendar_coloring.config import Config
from calendar_coloring.presets import POLIMI_ENROLLMENT, POLIMI_RULES
from calendar_coloring.profile import JsonProfileRepository, polimi_profile, serialize
from calendar_coloring.rules import Classifier
from calendar_coloring.sync.models import (
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

    def add_calendar(self, name: str, events: list[dict[str, Any]]) -> str:
        cal_id = f"id::{name}"
        self.calendars[cal_id] = {"name": name, "events": copy.deepcopy(events)}
        return cal_id

    def events_of(self, name: str) -> list[dict[str, Any]]:
        return self.calendars[f"id::{name}"]["events"]

    # -- CalendarGateway -----------------------------------------------------

    def get_calendar_id_by_name(self, name: str) -> str | None:
        for cal_id, cal in self.calendars.items():
            if cal["name"] == name:
                return cal_id
        return None

    def create_calendar(self, name: str) -> str:
        self.created.append(name)
        return self.add_calendar(name, [])

    def get_all_events(
        self, calendar_id: str, expand_recurring: bool = True
    ) -> list[dict[str, Any]]:
        self.listings.append((calendar_id, expand_recurring))
        return copy.deepcopy(self.calendars[calendar_id]["events"])

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
