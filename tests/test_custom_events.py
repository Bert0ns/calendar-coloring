"""Tests for custom events and calendar adoption."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import FakeCalendarGateway, polimi_profile

from unical.cli import main as cli
from unical.custom_events import (
    CUSTOM_PROPERTY,
    MANAGED_PROPERTY,
    CustomEvent,
    adopt_target_event,
    is_custom_event,
    sanitize_custom_id,
)
from unical.events import Event
from unical.palette import GoogleColor
from unical.profile import JsonProfileRepository
from unical.strategies import EventColoringStrategy
from unical.sync.models import MutationAction
from unical.sync.planner import SyncPlanner


class DummyStrategy(EventColoringStrategy):
    def determine_color(self, event: Event) -> GoogleColor | None:
        return None


def test_sanitize_custom_id() -> None:
    # Random ID generation when None
    cid = sanitize_custom_id()
    assert cid.startswith("cst")
    assert len(cid) > 5

    # Valid base32hex IDs are preserved
    assert sanitize_custom_id("abcde12345") == "abcde12345"

    # Uppercase letters are lowered
    assert sanitize_custom_id("ABCDE12345") == "abcde12345"

    # Invalid characters get cleaned and hashed
    dirty = "my-special-event@google.com"
    clean = sanitize_custom_id(dirty)
    assert "-" not in clean
    assert "@" not in clean
    assert clean.isalnum()


def test_custom_event_to_target_event() -> None:
    cst = CustomEvent(
        id="cst0123456789",
        summary="Study Group",
        description="Math review",
        location="Library Room 3",
        color_id="5",
        start={"dateTime": "2026-10-15T14:00:00+02:00"},
        end={"dateTime": "2026-10-15T16:00:00+02:00"},
        recurrence=("RRULE:FREQ=WEEKLY;BYDAY=TH",),
        source="created",
    )
    assert cst.start_day == date(2026, 10, 15)

    target_event = cst.to_target_event()
    assert target_event["id"] == "cst0123456789"
    assert target_event["summary"] == "Study Group"
    assert target_event["description"] == "Math review"
    assert target_event["location"] == "Library Room 3"
    assert target_event["colorId"] == "5"
    assert target_event["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TH"]

    private_props = target_event["extendedProperties"]["private"]
    assert private_props[MANAGED_PROPERTY] == "true"
    assert private_props[CUSTOM_PROPERTY] == "true"
    assert is_custom_event(target_event)


def test_adopt_target_event() -> None:
    unmanaged: Event = {
        "id": "unmanaged_event_99",
        "summary": "Hackathon Meeting",
        "description": "Discuss project ideas",
        "location": "Discord",
        "colorId": "7",
        "start": {"dateTime": "2026-10-20T18:00:00+02:00"},
        "end": {"dateTime": "2026-10-20T20:00:00+02:00"},
        "recurrence": ["RRULE:FREQ=WEEKLY"],
    }
    assert not is_custom_event(unmanaged)

    adopted = adopt_target_event(unmanaged)
    assert adopted.summary == "Hackathon Meeting"
    assert adopted.description == "Discuss project ideas"
    assert adopted.location == "Discord"
    assert adopted.color_id == "7"
    assert adopted.recurrence == ("RRULE:FREQ=WEEKLY",)
    assert adopted.source == "adopted"
    assert adopted.start_day == date(2026, 10, 20)


def test_sync_planner_inserts_custom_event() -> None:
    cst = CustomEvent(
        id="cst0123456789",
        summary="Study Group",
        start={"dateTime": "2026-10-15T14:00:00+02:00"},
        end={"dateTime": "2026-10-15T16:00:00+02:00"},
    )
    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[cst])
    plan = planner.plan(source_events=[], target_events=[], target_calendar_id="cal_1")

    assert len(plan.mutations) == 1
    mut = plan.mutations[0]
    assert mut.action == MutationAction.INSERT
    assert mut.event_id == "cst0123456789"
    assert mut.summary == "Study Group"
    assert is_custom_event(mut.body)


def test_sync_planner_updates_custom_event_when_modified() -> None:
    cst = CustomEvent(
        id="cst0123456789",
        summary="Updated Study Group",
        start={"dateTime": "2026-10-15T14:00:00+02:00"},
        end={"dateTime": "2026-10-15T16:00:00+02:00"},
    )
    existing: Event = {
        "id": "cst0123456789",
        "summary": "Old Study Group",
        "start": {"dateTime": "2026-10-15T14:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T16:00:00+02:00"},
        "extendedProperties": {
            "private": {
                MANAGED_PROPERTY: "true",
                CUSTOM_PROPERTY: "true",
            }
        },
    }
    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[cst])
    plan = planner.plan(
        source_events=[], target_events=[existing], target_calendar_id="cal_1"
    )

    assert len(plan.mutations) == 1
    mut = plan.mutations[0]
    assert mut.action == MutationAction.UPDATE
    assert mut.summary == "Updated Study Group"


def test_sync_planner_deletes_custom_event_removed_from_profile() -> None:
    existing_custom: Event = {
        "id": "cst0123456789",
        "summary": "Removed Study Group",
        "start": {"dateTime": "2026-10-15T14:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T16:00:00+02:00"},
        "extendedProperties": {
            "private": {
                MANAGED_PROPERTY: "true",
                CUSTOM_PROPERTY: "true",
            }
        },
    }
    # Planner with empty custom_events list
    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[])
    plan = planner.plan(
        source_events=[], target_events=[existing_custom], target_calendar_id="cal_1"
    )

    assert len(plan.mutations) == 1
    mut = plan.mutations[0]
    assert mut.action == MutationAction.DELETE
    assert mut.event_id == "cst0123456789"


def test_sync_planner_preserves_unmanaged_events() -> None:
    unmanaged: Event = {
        "id": "personal_doctor_appointment",
        "summary": "Doctor Appointment",
        "start": {"dateTime": "2026-10-15T14:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T15:00:00+02:00"},
    }
    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[])
    plan = planner.plan(
        source_events=[], target_events=[unmanaged], target_calendar_id="cal_1"
    )

    assert len(plan.mutations) == 0
    assert "Doctor Appointment" in plan.preserved_unmanaged


def test_sync_planner_adopts_existing_unmanaged_event() -> None:
    # When user adopts an unmanaged event, it gets added to custom_events
    unmanaged: Event = {
        "id": "abcde12345",
        "summary": "Project Sync",
        "start": {"dateTime": "2026-10-15T14:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T15:00:00+02:00"},
    }
    adopted = adopt_target_event(unmanaged)
    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[adopted])
    plan = planner.plan(
        source_events=[], target_events=[unmanaged], target_calendar_id="cal_1"
    )

    # Since unmanaged didn't have the custom tags, plan issues an UPDATE to tag it!
    assert len(plan.mutations) == 1
    mut = plan.mutations[0]
    assert mut.action == MutationAction.UPDATE
    assert mut.event_id == "abcde12345"
    assert is_custom_event(mut.body)


def test_custom_events_immune_to_course_scoping() -> None:
    # Scoped sync (e.g. --course "Physics") should NOT delete custom events
    cst = CustomEvent(
        id="cst0123456789",
        summary="Study Group",
        start={"dateTime": "2026-10-15T14:00:00+02:00"},
        end={"dateTime": "2026-10-15T16:00:00+02:00"},
    )
    existing: Event = cst.to_target_event()
    planner = SyncPlanner(
        strategy=DummyStrategy(),
        course="Physics",
        in_scope=lambda event: "Physics" in (event.get("summary") or ""),
        custom_events=[cst],
    )
    plan = planner.plan(
        source_events=[], target_events=[existing], target_calendar_id="cal_1"
    )

    # Custom event should be up to date and NOT deleted
    assert len(plan.mutations) == 0


# -- CLI tests for unical events ---------------------------------------------


def test_events_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["events", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "list" in out
    assert "add" in out
    assert "delete" in out
    assert "adopt" in out


def test_events_list_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    ret = cli.main(["events", "list"])
    assert ret == cli.EXIT_OK
    assert "No custom events in profile." in capsys.readouterr().out


def test_events_add_and_list_and_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    # Add custom event
    ret = cli.main(
        [
            "events",
            "add",
            "--summary",
            "Team Hackathon",
            "--start",
            "2026-10-15T10:00:00",
            "--end",
            "2026-10-15T18:00:00",
            "--location",
            "Building 14",
            "--color",
            "5",
            "--id",
            "csthack123",
        ]
    )
    assert ret == cli.EXIT_OK
    assert "Added custom event 'Team Hackathon' [csthack123]" in capsys.readouterr().out

    # List custom event
    ret = cli.main(["events", "list"])
    assert ret == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Team Hackathon" in out
    assert "csthack123" in out
    assert "Building 14" in out
    assert "color 5" in out

    # Delete custom event
    ret = cli.main(["events", "delete", "csthack123"])
    assert ret == cli.EXIT_OK
    assert "Deleted custom event 'csthack123'" in capsys.readouterr().out

    # Delete non-existent
    ret = cli.main(["events", "delete", "nonexistent"])
    assert ret == cli.EXIT_FAILURE


def test_events_adopt_with_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    target_cal_name = "Polimi Target"
    monkeypatch.setenv("TARGET_CALENDAR_NAME", target_cal_name)

    unmanaged_event: Event = {
        "id": "unmanaged99",
        "summary": "Doctor Appointment",
        "start": {"dateTime": "2026-10-15T11:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T12:00:00+02:00"},
    }
    fake_gateway = FakeCalendarGateway({target_cal_name: [unmanaged_event]})
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: fake_gateway),
    )
    fake_auth_inst = MagicMock()
    monkeypatch.setattr(cli, "Authenticator", fake_auth_inst)

    ret = cli.main(["events", "adopt", "--all"])
    assert ret == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Found 1 unmanaged event(s)" in out
    assert "Adopted 'Doctor Appointment'" in out
    assert "Saved 1 adopted event(s) to profile" in out

    # Listing should now show the adopted event
    ret = cli.main(["events", "list"])
    assert ret == cli.EXIT_OK
    assert "Doctor Appointment" in capsys.readouterr().out


def test_events_adopt_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_is_interactive_shell", lambda: True)

    target_cal_name = "Polimi Target"
    monkeypatch.setenv("TARGET_CALENDAR_NAME", target_cal_name)

    ev1: Event = {
        "id": "unmanaged1",
        "summary": "Office Hours",
        "start": {"dateTime": "2026-10-15T11:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T12:00:00+02:00"},
    }
    ev2: Event = {
        "id": "unmanaged2",
        "summary": "Lunch with Bob",
        "start": {"dateTime": "2026-10-15T13:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T14:00:00+02:00"},
    }
    fake_gateway = FakeCalendarGateway({target_cal_name: [ev1, ev2]})
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: fake_gateway),
    )
    fake_auth_inst = MagicMock()
    monkeypatch.setattr(cli, "Authenticator", fake_auth_inst)

    # Prompt: adopt ev1 (yes), do not adopt ev2 (no)
    answers = iter(["y", "n"])
    ret = cli.main(["events", "adopt"], prompt_fn=lambda _: next(answers))
    assert ret == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Adopted 'Office Hours'" in out
    assert "Lunch with Bob" not in out
    assert "Saved 1 adopted event(s)" in out


def test_adopt_target_event_preserves_external_id_and_exclusive_end() -> None:
    unmanaged: Event = {
        "id": "external-service_event_99_XYZ",
        "summary": "All-day conference",
        "start": {"date": "2026-10-15"},
    }
    adopted = adopt_target_event(unmanaged)
    assert adopted.id == "external-service_event_99_XYZ"
    assert adopted.start == {"date": "2026-10-15"}
    assert adopted.end == {"date": "2026-10-16"}


def test_sync_planner_adopts_unmanaged_event_with_arbitrary_id() -> None:
    unmanaged: Event = {
        "id": "custom-event_42-with-dashes",
        "summary": "Advising Session",
        "start": {"dateTime": "2026-10-15T14:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T15:00:00+02:00"},
    }
    adopted = adopt_target_event(unmanaged)
    assert adopted.id == "custom-event_42-with-dashes"

    planner = SyncPlanner(strategy=DummyStrategy(), custom_events=[adopted])
    plan = planner.plan(
        source_events=[], target_events=[unmanaged], target_calendar_id="cal_1"
    )
    assert len(plan.mutations) == 1
    assert plan.mutations[0].action == MutationAction.UPDATE
    assert plan.mutations[0].event_id == "custom-event_42-with-dashes"
    assert not plan.preserved_unmanaged


def test_events_add_all_day_defaults_end_date_next_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    ret = cli.main(
        [
            "events",
            "add",
            "--summary",
            "Conference Day",
            "--start",
            "2026-10-15",
            "--id",
            "confday1",
        ]
    )
    assert ret == cli.EXIT_OK
    profile = JsonProfileRepository(profile_path).load()
    assert len(profile.custom_events) == 1
    cst = profile.custom_events[0]
    assert cst.start == {"date": "2026-10-15"}
    assert cst.end == {"date": "2026-10-16"}


def test_events_adopt_non_interactive_requires_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_is_interactive_shell", lambda: False)

    target_cal_name = "Polimi Target"
    monkeypatch.setenv("TARGET_CALENDAR_NAME", target_cal_name)

    ev: Event = {
        "id": "unmanaged1",
        "summary": "Office Hours",
        "start": {"dateTime": "2026-10-15T11:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T12:00:00+02:00"},
    }
    fake_gateway = FakeCalendarGateway({target_cal_name: [ev]})
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: fake_gateway),
    )
    monkeypatch.setattr(cli, "Authenticator", MagicMock())

    ret = cli.main(["events", "adopt"])
    assert ret == cli.EXIT_FAILURE
    out = capsys.readouterr().out
    assert "Cannot prompt for event adoption in a non-interactive shell" in out


def test_events_adopt_by_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    target_cal_name = "Polimi Target"
    monkeypatch.setenv("TARGET_CALENDAR_NAME", target_cal_name)

    ev1: Event = {
        "id": "unmanaged1",
        "summary": "Office Hours",
        "start": {"dateTime": "2026-10-15T11:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T12:00:00+02:00"},
    }
    ev2: Event = {
        "id": "unmanaged2",
        "summary": "Lunch with Bob",
        "start": {"dateTime": "2026-10-15T13:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T14:00:00+02:00"},
    }
    fake_gateway = FakeCalendarGateway({target_cal_name: [ev1, ev2]})
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: fake_gateway),
    )
    monkeypatch.setattr(cli, "Authenticator", MagicMock())

    ret = cli.main(["events", "adopt", "--id", "unmanaged1"])
    assert ret == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Adopted 'Office Hours'" in out
    profile = JsonProfileRepository(profile_path).load()
    assert len(profile.custom_events) == 1
    assert profile.custom_events[0].id == "unmanaged1"


def test_events_add_timed_defaults_end_one_hour_later(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    ret = cli.main(
        [
            "events",
            "add",
            "--summary",
            "Project Sync",
            "--start",
            "2026-10-15T14:00:00",
            "--id",
            "sync1",
        ]
    )
    assert ret == cli.EXIT_OK
    profile = JsonProfileRepository(profile_path).load()
    assert len(profile.custom_events) == 1
    cst = profile.custom_events[0]
    assert cst.start == {"dateTime": "2026-10-15T14:00:00"}
    assert cst.end == {"dateTime": "2026-10-15T15:00:00"}


def test_events_add_rejects_end_before_or_equal_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    ret = cli.main(
        [
            "events",
            "add",
            "--summary",
            "Backwards Event",
            "--start",
            "2026-10-15T14:00:00",
            "--end",
            "2026-10-15T13:00:00",
        ]
    )
    assert ret == cli.EXIT_FAILURE
    assert "End time must be strictly after start time" in capsys.readouterr().out


def test_events_add_rejects_invalid_date_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    JsonProfileRepository(profile_path).save(polimi_profile())
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    ret = cli.main(
        [
            "events",
            "add",
            "--summary",
            "Invalid Date",
            "--start",
            "not-a-valid-date",
        ]
    )
    assert ret == cli.EXIT_FAILURE
    assert "Invalid start date format" in capsys.readouterr().out


def test_events_adopt_filters_already_adopted_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    initial_profile = polimi_profile()
    initial_profile.custom_events.append(
        CustomEvent(
            id="unmanaged1",
            summary="Office Hours",
            start={"dateTime": "2026-10-15T11:00:00+02:00"},
            end={"dateTime": "2026-10-15T12:00:00+02:00"},
            source="adopted",
        )
    )
    JsonProfileRepository(profile_path).save(initial_profile)
    monkeypatch.setenv("PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)

    target_cal_name = "Polimi Target"
    monkeypatch.setenv("TARGET_CALENDAR_NAME", target_cal_name)

    ev1: Event = {
        "id": "unmanaged1",
        "summary": "Office Hours",
        "start": {"dateTime": "2026-10-15T11:00:00+02:00"},
        "end": {"dateTime": "2026-10-15T12:00:00+02:00"},
    }
    fake_gateway = FakeCalendarGateway({target_cal_name: [ev1]})
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: fake_gateway),
    )
    monkeypatch.setattr(cli, "Authenticator", MagicMock())

    ret = cli.main(["events", "adopt", "--all"])
    assert ret == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "No unmanaged events found" in out
