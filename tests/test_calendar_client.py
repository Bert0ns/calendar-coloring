from unittest.mock import MagicMock

from calendar_client import CalendarClient


def test_get_calendar_id_by_name():
    mock_service = MagicMock()
    mock_service.calendarList().list().execute.return_value = {
        "items": [
            {"summary": "Other Calendar", "id": "other_id"},
            {"summary": "Polimi Calendar", "id": "polimi_id"},
        ],
        "nextPageToken": None,
    }

    client = CalendarClient(credentials=MagicMock())
    client.service = mock_service

    assert client.get_calendar_id_by_name("Polimi Calendar") == "polimi_id"
    assert client.get_calendar_id_by_name("Nonexistent") is None


def test_batch_mutate_events():
    mock_service = MagicMock()
    mock_batch = MagicMock()
    mock_service.new_batch_http_request.return_value = mock_batch

    client = CalendarClient(credentials=MagicMock())
    client.service = mock_service

    operations = [
        {"action": "insert", "calendar_id": "cal_1", "body": {"summary": "Event 1"}},
        {
            "action": "update",
            "calendar_id": "cal_1",
            "event_id": "e_2",
            "body": {"summary": "Event 2"},
        },
        {"action": "delete", "calendar_id": "cal_1", "event_id": "e_3"},
    ]

    # When execute is called on the mock batch, invoke callbacks registered with add()
    def mock_execute():
        for call_args in mock_batch.add.call_args_list:
            callback = call_args[1]["callback"]
            req_id = call_args[1]["request_id"]
            callback(req_id, {"status": "ok"}, None)

    mock_batch.execute.side_effect = mock_execute

    results = client.batch_mutate_events(operations, batch_size=2)

    # 3 operations with batch_size=2 means 2 batch requests executed
    assert mock_service.new_batch_http_request.call_count == 2
    assert len(results) == 3
    for _op, exc in results:
        assert exc is None
