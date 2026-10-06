import copy
from datetime import date

import pytest
from conftest import POLIMI

from calendar_coloring.events import Event
from calendar_coloring.palette import GoogleColor
from calendar_coloring.strategies import EventColoringStrategy
from calendar_coloring.sync.models import ColorOrigin, MutationAction
from calendar_coloring.sync.planner import (
    MANAGED_PROPERTY,
    SyncPlanner,
    build_target_event,
    has_current_tag,
    is_managed,
    needs_update,
    sanitize_event_id,
    start_day,
)

MANAGED = {"private": {MANAGED_PROPERTY: "true"}}


class FixedStrategy(EventColoringStrategy):
    def __init__(self, color: GoogleColor | None = GoogleColor.BANANA) -> None:
        self.color = color

    def determine_color(self, event: Event) -> GoogleColor | None:
        return self.color


def source_event(**overrides) -> Event:
    event: Event = {
        "id": "event12345",
        "summary": "Lezione: Didattica - CS",
        "description": "desc",
        "location": "Room 1",
        "start": {"dateTime": "2026-09-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-09-20T12:00:00+02:00"},
    }
    event.update(overrides)
    return event


def synced_target(source: Event, color_id: str | None = "5") -> Event:
    return build_target_event(
        source, "event12345", POLIMI.target_title(source), color_id
    )


def planner(color: GoogleColor | None = GoogleColor.BANANA) -> SyncPlanner:
    return SyncPlanner(FixedStrategy(color), POLIMI.target_title)


# -- sanitize_event_id -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "sanitized"),
    [
        ("abcdef12345", "abcdef12345"),
        ("ABCDEF12345", "abcdef12345"),
        ("vvvvv", "vvvvv"),
        ("1172587polimiit", "1172587polimiit"),
        # Values produced by main's implementation (must stay stable, or every
        # synced event would be deleted and re-created).
        ("exam0005", "eam0005d070c1f6"),
        ("1172587-polimi.it", "1172587polimiit284d64df"),
    ],
)
def test_sanitize_event_id(raw: str, sanitized: str) -> None:
    assert sanitize_event_id(raw) == sanitized


def test_sanitize_altered_ids_get_a_hash_suffix() -> None:
    sanitized = sanitize_event_id("abc!@#def123")
    assert sanitized.startswith("abcdef123")
    assert len(sanitized) == len("abcdef123") + 8
    assert set(sanitized) <= set("abcdefghijklmnopqrstuv0123456789")


def test_sanitize_avoids_collisions() -> None:
    assert sanitize_event_id("ab-c1") != sanitize_event_id("abc1")
    assert sanitize_event_id("ABC_12") != sanitize_event_id("abc-12")


def test_sanitize_respects_max_length() -> None:
    assert len(sanitize_event_id("a" * 2000)) == 1024
    assert len(sanitize_event_id("a-" * 1000)) == 1000 + 8
    assert len(sanitize_event_id("a-" * 2000)) == 1024


@pytest.mark.parametrize("raw", ["ab1", "", "wxyz!", "____"])
def test_sanitize_short_ids_uses_stable_hash(raw: str) -> None:
    sanitized = sanitize_event_id(raw)
    assert sanitized.startswith("polimi")
    assert len(sanitized) == 22
    assert set(sanitized) <= set("abcdefghijklmnopqrstuv0123456789")
    assert sanitize_event_id(raw) == sanitized


# -- is_managed / build_target_event / needs_update --------------------------


@pytest.mark.parametrize(
    ("event", "managed"),
    [
        ({"extendedProperties": MANAGED}, True),
        ({"extendedProperties": {"private": {MANAGED_PROPERTY: "false"}}}, False),
        ({"extendedProperties": {"shared": {MANAGED_PROPERTY: "true"}}}, False),
        ({"extendedProperties": {"private": None}}, False),
        ({"extendedProperties": None}, False),
        ({}, False),
    ],
)
def test_is_managed(event: Event, managed: bool) -> None:
    assert is_managed(event) is managed


def test_build_target_event() -> None:
    body = build_target_event(source_event(), "event12345", "CS", "5")
    assert body == {
        "id": "event12345",
        "summary": "CS",
        "description": "desc",
        "location": "Room 1",
        "start": {"dateTime": "2026-09-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-09-20T12:00:00+02:00"},
        "colorId": "5",
        "extendedProperties": MANAGED,
    }


def test_build_target_event_without_optional_fields() -> None:
    source = source_event()
    del source["location"], source["description"]
    body = build_target_event(source, "event12345", "CS", None)
    assert "location" not in body
    assert "colorId" not in body
    assert body["description"] == ""


def test_build_target_event_does_not_share_managed_dict() -> None:
    a = build_target_event(source_event(), "a", "A", None)
    a["extendedProperties"]["private"]["x"] = "y"
    b = build_target_event(source_event(), "b", "B", None)
    assert "x" not in b["extendedProperties"]["private"]


def test_needs_update_false_for_identical_event() -> None:
    desired = synced_target(source_event())
    assert not needs_update(copy.deepcopy(desired), desired)


def test_needs_update_ignores_unsynced_fields() -> None:
    desired = synced_target(source_event())
    existing = {**copy.deepcopy(desired), "etag": "x", "htmlLink": "y", "status": "c"}
    assert not needs_update(existing, desired)


def test_needs_update_treats_date_and_datetime_by_value() -> None:
    desired = synced_target(source_event(start={"date": "2026-09-20"}))
    existing = copy.deepcopy(desired)
    existing["start"] = {"date": "2026-09-20", "timeZone": "Europe/Rome"}
    assert not needs_update(existing, desired)


@pytest.mark.parametrize(
    "change",
    [
        {"summary": "Other"},
        {"description": "changed"},
        {"location": "Room 2"},
        {"colorId": "6"},
        {"start": {"dateTime": "2026-09-20T11:00:00+02:00"}},
        {"end": {"dateTime": "2026-09-20T13:00:00+02:00"}},
        {"start": {"date": "2026-09-20"}},
        {"extendedProperties": {}},
    ],
)
def test_needs_update_detects_each_synced_field(change: Event) -> None:
    desired = synced_target(source_event())
    existing = {**copy.deepcopy(desired), **change}
    assert needs_update(existing, desired)


def test_needs_update_when_location_or_color_removed() -> None:
    desired = synced_target(source_event(), color_id=None)
    del desired["location"]
    existing = synced_target(source_event(), color_id="5")
    assert needs_update(existing, desired)


# -- SyncPlanner -------------------------------------------------------------


def test_plan_inserts_new_events_with_cleaned_title_and_color() -> None:
    plan = planner().plan([source_event()], [], "tgt")

    assert plan.target_calendar_id == "tgt"
    assert [m.action for m in plan.mutations] == [MutationAction.INSERT]
    mutation = plan.mutations[0]
    assert mutation.summary == "CS"
    assert mutation.body is not None
    assert mutation.body["colorId"] == "5"
    assert mutation.body["summary"] == "CS"
    assert is_managed(mutation.body)
    decision = plan.decisions[0]
    assert decision.color_origin is ColorOrigin.STRATEGY
    assert decision.source_summary == "Lezione: Didattica - CS"
    assert decision.target_summary == "CS"


def test_plan_without_title_function_keeps_titles() -> None:
    plan = SyncPlanner(FixedStrategy()).plan([source_event()], [], "tgt")
    assert plan.mutations[0].summary == "Lezione: Didattica - CS"


def test_plan_skips_identical_events() -> None:
    source = source_event()
    plan = planner().plan([source], [synced_target(source)], "tgt")
    assert plan.mutations == ()
    assert plan.unchanged == 1
    assert plan.decisions[0].action is None


def test_plan_updates_changed_events() -> None:
    source = source_event()
    target = synced_target(source, color_id="1")
    plan = planner().plan([source], [target], "tgt")
    assert [(m.action, m.event_id) for m in plan.mutations] == [
        (MutationAction.UPDATE, "event12345")
    ]


def test_plan_adopts_untagged_event_with_same_id() -> None:
    source = source_event()
    target = synced_target(source)
    del target["extendedProperties"]
    plan = planner().plan([source], [target], "tgt")
    assert plan.mutations[0].action is MutationAction.UPDATE
    assert is_managed(plan.mutations[0].body)


def test_plan_preserves_existing_color_when_no_strategy_matches() -> None:
    source = source_event()
    target = synced_target(source, color_id="9")
    plan = planner(color=None).plan([source], [target], "tgt")
    assert plan.mutations == ()
    assert plan.decisions[0].color_origin is ColorOrigin.PRESERVED
    assert plan.decisions[0].color_id == "9"


def test_plan_uses_default_color_when_nothing_applies() -> None:
    plan = planner(color=None).plan([source_event()], [], "tgt")
    assert "colorId" not in plan.mutations[0].body
    assert plan.decisions[0].color_origin is ColorOrigin.DEFAULT


def test_plan_deletes_only_stale_managed_events() -> None:
    managed_stale = {"id": "old12345", "summary": "Old", "extendedProperties": MANAGED}
    untitled_stale = {"id": "old67890", "extendedProperties": MANAGED}
    personal = {"id": "dentist01", "summary": "Dentist"}
    plan = planner().plan([], [managed_stale, untitled_stale, personal], "tgt")

    assert [(m.action, m.event_id, m.summary) for m in plan.mutations] == [
        (MutationAction.DELETE, "old12345", "Old"),
        (MutationAction.DELETE, "old67890", "old67890"),
    ]
    assert plan.preserved_unmanaged == ("Dentist",)


def test_plan_matches_target_by_sanitized_id() -> None:
    source = source_event(id="EVENT_12345")
    target = {**synced_target(source), "id": sanitize_event_id("EVENT_12345")}
    plan = planner().plan([source], [target], "tgt")
    assert plan.mutations == ()


def test_plan_ignores_events_without_id() -> None:
    plan = planner().plan(
        [{"summary": "no id"}, source_event(id="")],
        [{"summary": "no id either"}],
        "tgt",
    )
    assert plan.mutations == ()
    assert plan.decisions == ()


def test_plan_counts_and_ordering() -> None:
    sources = [source_event(id=f"event{i:05d}") for i in range(3)]
    unchanged = build_target_event(sources[0], "event00000", "CS", "5")
    changed = {
        **build_target_event(sources[1], "event00001", "CS", "5"),
        "summary": "x",
    }
    stale = {"id": "stale0001", "summary": "S", "extendedProperties": MANAGED}
    plan = planner().plan(sources, [unchanged, changed, stale], None)

    assert [m.action for m in plan.mutations] == [
        MutationAction.UPDATE,
        MutationAction.INSERT,
        MutationAction.DELETE,
    ]
    assert plan.count(MutationAction.INSERT) == 1
    assert plan.count(MutationAction.UPDATE) == 1
    assert plan.count(MutationAction.DELETE) == 1
    assert plan.unchanged == 1
    assert plan.source_event_count == 3
    assert plan.target_event_count == 3
    assert plan.target_calendar_id is None
    assert not plan.is_empty


def test_plan_does_not_mutate_inputs() -> None:
    sources = [source_event()]
    targets = [{"id": "stale0001", "extendedProperties": MANAGED}]
    snapshot = copy.deepcopy((sources, targets))
    planner().plan(sources, targets, "tgt")
    assert (sources, targets) == snapshot


def test_empty_plan() -> None:
    plan = planner().plan([], [], "tgt")
    assert plan.is_empty
    assert plan.unchanged == 0


# -- ported features & fixes ------------------------------------------------


def test_events_without_start_are_skipped_and_reported() -> None:
    plan = planner().plan(
        [{"id": "nostart12345", "summary": "Broken"}, {"id": "nostart67890"}],
        [],
        "tgt",
    )
    assert plan.mutations == ()
    assert plan.skipped_without_start == ("Broken", "nostart67890")


def test_recurrence_is_copied_and_compared() -> None:
    source = source_event(recurrence=["RRULE:FREQ=WEEKLY;COUNT=10"])
    target = synced_target(source_event())  # same event, but not recurring
    plan = planner().plan([source], [target], "tgt")
    assert plan.mutations[0].action is MutationAction.UPDATE
    assert plan.mutations[0].body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=10"]

    synced = synced_target(source)
    assert planner().plan([source], [synced], "tgt").mutations == ()


@pytest.mark.parametrize("field_name", ["description", "location"])
def test_missing_text_field_equals_empty_one(field_name: str) -> None:
    """Google omits empty fields: this must not cause an update on every run."""
    source = source_event(**{field_name: ""})
    target = synced_target(source)
    del target[field_name]
    assert planner().plan([source], [target], "tgt").mutations == ()


def test_recurring_instances_in_target_are_ignored() -> None:
    master = synced_target(source_event(recurrence=["RRULE:FREQ=WEEKLY"]))
    instance = {
        **master,
        "id": "event12345_20260927T080000Z",
        "recurringEventId": "event12345",
    }
    del instance["recurrence"]
    source = source_event(recurrence=["RRULE:FREQ=WEEKLY"])

    plan = planner().plan([source], [master, instance], "tgt")

    assert plan.mutations == ()  # neither re-inserted nor deleted as stale


class TestPruneBefore:
    CUTOFF = date(2026, 9, 22)

    def planner(self) -> SyncPlanner:
        return SyncPlanner(FixedStrategy(None), POLIMI.target_title, self.CUTOFF)

    def event(self, event_id: str, day: str, managed: bool = True) -> Event:
        event: Event = {
            "id": event_id,
            "summary": event_id,
            "start": {"date": day},
            "end": {"date": day},
        }
        if managed:
            event["extendedProperties"] = MANAGED
        return event

    def test_old_source_events_are_not_synced_and_their_copies_deleted(self) -> None:
        old = self.event("old12345", "2026-09-21")
        new = self.event("recent12345", "2026-09-22")
        plan = self.planner().plan([old, new], [old, new], "tgt")
        assert [(m.action, m.event_id) for m in plan.mutations] == [
            (MutationAction.DELETE, "old12345")
        ]
        assert plan.pruned == 1
        assert plan.prune_before == self.CUTOFF

    def test_old_managed_events_missing_from_source_count_as_pruned(self) -> None:
        plan = self.planner().plan([], [self.event("old12345", "2020-01-01")], "t")
        assert plan.count(MutationAction.DELETE) == 1
        assert plan.pruned == 1

    def test_stale_recent_events_are_deleted_but_not_counted_as_pruned(self) -> None:
        plan = self.planner().plan([], [self.event("recent12345", "2027-01-01")], "t")
        assert plan.count(MutationAction.DELETE) == 1
        assert plan.pruned == 0

    def test_unmanaged_old_events_are_never_deleted(self) -> None:
        personal = self.event("personal1", "2020-01-01", managed=False)
        plan = self.planner().plan([], [personal], "tgt")
        assert plan.mutations == ()
        assert plan.preserved_unmanaged == ("personal1",)

    def test_event_moved_after_cutoff_is_updated_not_deleted(self) -> None:
        """An event rescheduled past the cutoff must be updated only (the
        previous implementation also queued a delete for the same ID)."""
        moved_source = self.event("moved1234", "2026-10-01", managed=False)
        old_copy = self.event("moved1234", "2026-09-01")
        plan = self.planner().plan([moved_source], [old_copy], "tgt")
        assert [(m.action, m.event_id) for m in plan.mutations] == [
            (MutationAction.UPDATE, "moved1234")
        ]

    def test_events_without_parseable_start_are_kept(self) -> None:
        weird = {**self.event("weird1234", "2020-01-01"), "start": {"date": "nope"}}
        assert self.planner().is_syncable(weird)

    def test_syncable_filters_like_plan(self) -> None:
        events = [
            self.event("old12345", "2020-01-01"),
            self.event("recent12345", "2027-01-01"),
            {"id": "nostart12345"},
            {"start": {"date": "2027-01-01"}},
        ]
        assert [e["id"] for e in self.planner().syncable(events)] == ["recent12345"]


def test_start_day() -> None:
    assert start_day({"start": {"dateTime": "2026-09-20T10:00:00+02:00"}}) == date(
        2026, 9, 20
    )
    assert start_day({"start": {"date": "2026-09-20"}}) == date(2026, 9, 20)
    assert start_day({"start": {"date": "garbage"}}) is None
    assert start_day({}) is None


# -- legacy tag migration ----------------------------------------------------

LEGACY = {"private": {"polimi_sync_managed": "true"}}


def test_legacy_tag_is_managed_but_not_current() -> None:
    legacy = {"extendedProperties": LEGACY}
    both = {
        "extendedProperties": {"private": {**LEGACY["private"], **MANAGED["private"]}}
    }
    assert is_managed(legacy)
    assert not has_current_tag(legacy)
    assert is_managed(both)
    assert not has_current_tag(both)
    assert has_current_tag({"extendedProperties": MANAGED})


def test_legacy_tagged_events_are_updated_once_with_the_current_tag() -> None:
    source = source_event()
    legacy = synced_target(source)
    legacy["extendedProperties"] = copy.deepcopy(LEGACY)

    plan = planner().plan([source], [legacy], "cal")

    [mutation] = plan.mutations
    assert mutation.action is MutationAction.UPDATE
    assert mutation.body is not None
    assert mutation.body["extendedProperties"] == MANAGED
    # Once migrated, nothing changes any more.
    assert planner().plan([source], [mutation.body], "cal").mutations == ()


def test_legacy_tagged_events_removed_from_the_source_are_deleted() -> None:
    stale = {"id": "stale0001", "summary": "Old", "extendedProperties": LEGACY}
    plan = planner().plan([], [stale], "cal")
    assert [m.action for m in plan.mutations] == [MutationAction.DELETE]
    assert plan.preserved_unmanaged == ()
