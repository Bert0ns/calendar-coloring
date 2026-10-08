from unittest.mock import MagicMock

import pytest

from unical.google_client import GoogleCalendarClient
from unical.sync.models import CalendarInfo, Mutation, MutationAction


def make_client(sleeps: list[float] | None = None):
    service = MagicMock()
    sleep = sleeps.append if sleeps is not None else (lambda _: None)
    return GoogleCalendarClient(service, sleep=sleep), service


def test_get_calendar_id_by_name_follows_pagination() -> None:
    client, service = make_client()
    service.calendarList().list().execute.side_effect = [
        {"items": [{"summary": "Other", "id": "other"}], "nextPageToken": "p2"},
        {"items": [{"summary": "School", "id": "school"}]},
    ]
    assert client.get_calendar_id_by_name("School") == "school"


def test_get_calendar_id_by_name_returns_none_when_missing() -> None:
    client, service = make_client()
    service.calendarList().list().execute.return_value = {"items": []}
    assert client.get_calendar_id_by_name("Nope") is None


def test_create_calendar_in_the_given_time_zone() -> None:
    client, service = make_client()
    service.calendars().insert().execute.return_value = {"id": "new"}
    assert client.create_calendar("Tgt", "America/New_York") == "new"
    service.calendars().insert.assert_called_with(
        body={"summary": "Tgt", "timeZone": "America/New_York"}
    )
    service.calendars().get.assert_not_called()


@pytest.mark.parametrize(
    ("primary", "expected"),
    [({"timeZone": "Europe/Berlin"}, "Europe/Berlin"), ({}, "UTC")],
)
def test_create_calendar_uses_the_primary_calendar_time_zone(
    primary: dict[str, str], expected: str
) -> None:
    client, service = make_client()
    service.calendars().get().execute.return_value = primary
    service.calendars().insert().execute.return_value = {"id": "new"}
    assert client.create_calendar("Tgt") == "new"
    service.calendars().get.assert_called_with(calendarId="primary")
    service.calendars().insert.assert_called_with(
        body={"summary": "Tgt", "timeZone": expected}
    )


def test_get_all_events_follows_pagination() -> None:
    client, service = make_client()
    service.events().list().execute.side_effect = [
        {"items": [{"id": "a"}], "nextPageToken": "p2"},
        {"items": [{"id": "b"}]},
    ]
    assert client.get_all_events("cal") == [{"id": "a"}, {"id": "b"}]
    service.events().list.assert_called_with(
        calendarId="cal", singleEvents=True, orderBy="startTime", pageToken="p2"
    )


def test_get_all_events_without_expanding_recurring_events() -> None:
    client, service = make_client()
    service.events().list().execute.return_value = {"items": []}
    client.get_all_events("cal", expand_recurring=False)
    # orderBy=startTime is rejected by the API unless singleEvents=True.
    service.events().list.assert_called_with(
        calendarId="cal", singleEvents=False, pageToken=None
    )


def install_batch(service: MagicMock, errors: dict[str, Exception] | None = None):
    """Makes every batch invoke the registered callbacks on execute()."""
    batches = []

    def new_batch():
        batch = MagicMock()

        def execute():
            for call in batch.add.call_args_list:
                req_id = call.kwargs["request_id"]
                if errors is not None and req_id == "skip":
                    continue
                call.kwargs["callback"](req_id, {}, (errors or {}).get(req_id))

        batch.execute.side_effect = execute
        batches.append(batch)
        return batch

    service.new_batch_http_request.side_effect = new_batch
    return batches


MUTATIONS = [
    Mutation(MutationAction.INSERT, "e1", "E1", {"id": "e1"}),
    Mutation(MutationAction.UPDATE, "e2", "E2", {"id": "e2"}),
    Mutation(MutationAction.DELETE, "e3", "E3"),
]


def test_batch_mutate_events_chunks_and_maps_actions() -> None:
    client, service = make_client()
    batches = install_batch(service)

    results = client.batch_mutate_events("cal", MUTATIONS, batch_size=2)

    assert len(batches) == 2
    assert [len(b.add.call_args_list) for b in batches] == [2, 1]
    assert [r.mutation for r in results] == MUTATIONS
    assert all(r.ok for r in results)
    service.events().insert.assert_called_with(calendarId="cal", body={"id": "e1"})
    service.events().update.assert_called_with(
        calendarId="cal", eventId="e2", body={"id": "e2"}
    )
    service.events().delete.assert_called_with(calendarId="cal", eventId="e3")


def test_batch_mutate_events_propagates_per_request_errors() -> None:
    client, service = make_client()
    error = RuntimeError("quota")
    install_batch(service, errors={"op_1": error})

    results = client.batch_mutate_events("cal", MUTATIONS)

    assert [r.error for r in results] == [None, error, None]


def test_batch_mutate_events_flags_missing_callbacks() -> None:
    client, service = make_client()
    batch = MagicMock()
    service.new_batch_http_request.return_value = batch  # execute() calls nothing

    results = client.batch_mutate_events("cal", MUTATIONS[:1])

    assert isinstance(results[0].error, RuntimeError)


def test_batch_mutate_events_with_no_mutations_does_nothing() -> None:
    client, service = make_client()
    assert client.batch_mutate_events("cal", []) == []
    service.new_batch_http_request.assert_not_called()


def test_batch_mutate_events_rejects_invalid_batch_size() -> None:
    client, _ = make_client()
    with pytest.raises(ValueError):
        client.batch_mutate_events("cal", MUTATIONS, batch_size=0)


def test_from_credentials_builds_calendar_service(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "unical.google_client.build",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "service",
    )
    client = GoogleCalendarClient.from_credentials("creds")
    assert client.service == "service"
    assert calls == [(("calendar", "v3"), {"credentials": "creds"})]


def test_batch_is_retried_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    client, service = make_client(sleeps)
    batches = install_batch(service)
    attempts = {"n": 0}
    original_new = service.new_batch_http_request.side_effect

    def flaky_batch():
        batch = original_new()
        succeed = batch.execute.side_effect

        def execute():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient")
            succeed()

        batch.execute.side_effect = execute
        return batch

    service.new_batch_http_request.side_effect = flaky_batch

    results = client.batch_mutate_events("cal", MUTATIONS)

    assert all(r.ok for r in results)
    assert attempts["n"] == 3
    assert sleeps == [1, 2]
    assert len(batches) == 3
    assert len(set(batches)) == 3


def test_batch_gives_up_after_retries_and_fails_the_whole_chunk() -> None:
    sleeps: list[float] = []
    client, service = make_client(sleeps)
    batch = MagicMock()
    error = ConnectionError("down")
    batch.execute.side_effect = error
    service.new_batch_http_request.return_value = batch

    results = client.batch_mutate_events("cal", MUTATIONS)

    assert batch.execute.call_count == 4
    assert sleeps == [1, 2, 4]
    assert [r.error for r in results] == [error, error, error]


def test_failed_chunk_does_not_affect_other_chunks() -> None:
    client, service = make_client()
    good, bad = MagicMock(), MagicMock()
    bad.execute.side_effect = ConnectionError("down")

    def run_callbacks():
        for call in good.add.call_args_list:
            call.kwargs["callback"](call.kwargs["request_id"], {}, None)

    good.execute.side_effect = run_callbacks
    service.new_batch_http_request.side_effect = [bad] * 4 + [good]

    results = client.batch_mutate_events("cal", MUTATIONS, batch_size=2)

    assert [r.ok for r in results] == [False, False, True]


def test_batch_retry_clears_partial_callback_state() -> None:
    sleeps: list[float] = []
    client, service = make_client(sleeps)
    attempts = 0
    created_batches = []

    def make_batch():
        nonlocal attempts
        attempts += 1
        batch = MagicMock()
        created_batches.append(batch)
        attempt_num = attempts

        def execute():
            if attempt_num == 1:
                first_call = batch.add.call_args_list[0]
                first_call.kwargs["callback"](
                    first_call.kwargs["request_id"], {}, RuntimeError("temp error")
                )
                raise ConnectionError("reset by peer")
            for call in batch.add.call_args_list:
                call.kwargs["callback"](call.kwargs["request_id"], {}, None)

        batch.execute.side_effect = execute
        return batch

    service.new_batch_http_request.side_effect = make_batch

    results = client.batch_mutate_events("cal", MUTATIONS)

    assert len(created_batches) == 2
    assert all(r.ok for r in results)
    assert [r.error for r in results] == [None, None, None]


def test_execute_with_retries_accepts_batch_instance() -> None:
    client, _ = make_client()
    batch = MagicMock()
    batch.execute.side_effect = [ConnectionError("retry me"), None]
    err = client._execute_with_retries(batch)
    assert err is None
    assert batch.execute.call_count == 2


def test_list_calendars_follows_pagination_and_reads_access() -> None:
    client, service = make_client()
    service.calendarList().list().execute.side_effect = [
        {
            "items": [
                {"id": "me", "summary": "Me", "accessRole": "owner", "primary": True},
                {"id": "uni", "summary": "Polimi", "accessRole": "reader"},
            ],
            "nextPageToken": "p2",
        },
        {
            "items": [
                {"id": "shared", "summary": "Shared", "accessRole": "writer"},
                {"id": "busy", "accessRole": "freeBusyReader"},
            ]
        },
    ]
    assert client.list_calendars() == [
        CalendarInfo("me", "Me", writable=True, primary=True),
        CalendarInfo("uni", "Polimi", writable=False),
        CalendarInfo("shared", "Shared", writable=True),
        CalendarInfo("busy", "busy", writable=False),
    ]
