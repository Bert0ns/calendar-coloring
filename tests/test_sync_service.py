from datetime import date
from unittest.mock import patch

import pytest
from conftest import POLIMI, FakeCalendarGateway

from unical.palette import GoogleColor
from unical.strategies import EventColoringStrategy
from unical.sync import (
    GoogleCalendarSource,
    MutationAction,
    SourceCalendarNotFoundError,
    SourceError,
    SyncPlanner,
    SyncService,
)

SOURCE = [
    {
        "id": "event12345",
        "summary": "Lezione: Didattica - CS",
        "start": {"date": "2026-09-20"},
        "end": {"date": "2026-09-21"},
    }
]


class Banana(EventColoringStrategy):
    def determine_color(self, event):
        return GoogleColor.BANANA


PLANNER = SyncPlanner(Banana(), POLIMI.target_title)


def test_google_source_fetches_expanded_events() -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    source = GoogleCalendarSource(gateway, "Src")
    assert source.label == "'Src'"
    assert source.fetch_events() == SOURCE
    assert gateway.listings == [("id::Src", True)]


def test_google_source_raises_when_missing() -> None:
    source = GoogleCalendarSource(FakeCalendarGateway(), "Nope")
    with pytest.raises(SourceCalendarNotFoundError) as exc_info:
        source.fetch_events()
    assert exc_info.value.name == "Nope"
    assert str(exc_info.value) == "Source calendar 'Nope' not found."
    assert isinstance(exc_info.value, SourceError)


def test_plan_lists_target_recurring_events_as_masters() -> None:
    gateway = FakeCalendarGateway({"Tgt": []})
    service = SyncService(gateway)
    service.plan(PLANNER, SOURCE, service.find_target_calendar("Tgt"))
    assert gateway.listings == [("id::Tgt", False)]


def test_plan_on_missing_target_is_read_only() -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    service = SyncService(gateway)

    plan = service.plan(PLANNER, SOURCE, service.find_target_calendar("Tgt"))

    assert plan.target_calendar_id is None
    assert [m.action for m in plan.mutations] == [MutationAction.INSERT]
    assert gateway.created == []
    assert gateway.batches == []


def test_apply_creates_missing_target_calendar() -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    service = SyncService(gateway)
    plan = service.plan(PLANNER, SOURCE, None)

    result = service.apply(plan, "Tgt")

    assert gateway.created == ["Tgt"]
    assert result.created_target_calendar
    assert result.inserted == 1
    assert gateway.events_of("Tgt")[0]["summary"] == "CS"


def test_apply_creates_target_even_without_mutations() -> None:
    gateway = FakeCalendarGateway({"Src": []})
    service = SyncService(gateway)
    result = service.apply(service.plan(PLANNER, [], None), "Tgt")
    assert gateway.created == ["Tgt"]
    assert gateway.batches == []
    assert result.succeeded


def test_apply_skips_batch_when_nothing_to_do() -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    service = SyncService(gateway)
    target_id = service.find_target_calendar("Tgt")
    service.apply(service.plan(PLANNER, SOURCE, target_id), "Tgt")
    gateway.batches.clear()

    result = service.apply(service.plan(PLANNER, SOURCE, target_id), "Tgt")

    assert gateway.batches == []
    assert not result.created_target_calendar
    assert (result.inserted, result.updated, result.deleted) == (0, 0, 0)


def test_apply_reports_failures_and_counts_only_successes() -> None:
    sources = [dict(SOURCE[0], id="event00001"), dict(SOURCE[0], id="event00002")]
    gateway = FakeCalendarGateway({"Src": sources, "Tgt": []})
    gateway.fail_event_ids = {"event00002"}
    service = SyncService(gateway)

    result = service.apply(
        service.plan(PLANNER, sources, service.find_target_calendar("Tgt")), "Tgt"
    )

    assert result.inserted == 1
    assert not result.succeeded
    assert [f.mutation.event_id for f in result.failures] == ["event00002"]
    assert str(result.failures[0].error) == "boom"


def test_apply_reports_progress_per_chunk() -> None:
    from unical.sync.service import PROGRESS_CHUNK_SIZE

    events = [
        {**SOURCE[0], "id": f"event{i:05d}"} for i in range(PROGRESS_CHUNK_SIZE + 3)
    ]
    gateway = FakeCalendarGateway({"Tgt": []})
    service = SyncService(gateway)
    progress: list[tuple[int, int]] = []

    result = service.apply(
        service.plan(PLANNER, events, service.find_target_calendar("Tgt")),
        "Tgt",
        on_progress=lambda done, total: progress.append((done, total)),
    )

    total = PROGRESS_CHUNK_SIZE + 3
    assert result.inserted == total
    assert progress == [(PROGRESS_CHUNK_SIZE, total), (total, total)]
    assert [len(batch) for _, batch in gateway.batches] == [PROGRESS_CHUNK_SIZE, 3]


def test_plan_passes_window_bounds_and_respects_prune_before() -> None:
    gateway = FakeCalendarGateway({"Tgt": []})
    service = SyncService(gateway)
    with patch.object(
        gateway, "get_all_events", wraps=gateway.get_all_events
    ) as mock_get:
        service.plan(
            PLANNER,
            SOURCE,
            "id::Tgt",
            time_min=date(2026, 3, 1),
            time_max=date(2026, 9, 15),
        )
        mock_get.assert_called_with(
            "id::Tgt",
            expand_recurring=False,
            time_min=date(2026, 3, 1),
            time_max=date(2026, 9, 15),
        )

    planner_with_prune = SyncPlanner(
        Banana(), POLIMI.target_title, prune_before=date(2026, 1, 1)
    )
    with patch.object(
        gateway, "get_all_events", wraps=gateway.get_all_events
    ) as mock_get:
        service.plan(
            planner_with_prune,
            SOURCE,
            "id::Tgt",
            time_min=date(2026, 3, 1),
            time_max=date(2026, 9, 15),
        )
        mock_get.assert_called_with(
            "id::Tgt", expand_recurring=False, time_min=None, time_max=date(2026, 9, 15)
        )
