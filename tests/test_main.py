from datetime import date

import pytest

from main import mask_url, parse_args


def test_parse_args_defaults():
    args = parse_args([])
    assert args.target == "all"
    assert args.verbose is False
    assert args.quiet is False
    assert args.interactive is False
    assert args.ical_url is None
    assert args.dry_run is False
    assert args.prune_before is None


def test_parse_args_all_options():
    args = parse_args(
        [
            "deadlines",
            "-v",
            "-i",
            "--ical",
            "https://example.com/feed.ics",
            "--dry-run",
            "--prune-before",
            "2025-01-01",
        ]
    )
    assert args.target == "deadlines"
    assert args.verbose is True
    assert args.interactive is True
    assert args.ical_url == "https://example.com/feed.ics"
    assert args.dry_run is True
    assert args.prune_before == "2025-01-01"


def test_parse_args_rejects_unknown_target():
    with pytest.raises(SystemExit):
        parse_args(["unknown"])


def test_mask_url_hides_token():
    masked = mask_url("https://ical-polimiapp.polimi.it/12345/secret-token-abc")
    assert "secret-token-abc" not in masked
    assert "12345" not in masked
    assert masked.startswith("https://ical-polimiapp.polimi.it/")


def test_mask_url_handles_garbage():
    assert mask_url("not a url") == "<redacted>"


def test_main_rejects_bad_prune_date(capsys):
    from unittest.mock import MagicMock, patch

    from main import main

    with (
        patch("main.Authenticator") as mock_auth,
        patch("main.CalendarClient") as mock_client,
    ):
        mock_auth.return_value.get_credentials.return_value = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            main(["--prune-before", "yesterday"])
    assert exc_info.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().out
    mock_client.assert_not_called()


def test_main_plumbs_flags_to_processor():
    from unittest.mock import MagicMock, patch

    from main import main

    with (
        patch("main.Authenticator") as mock_auth,
        patch("main.CalendarClient"),
        patch("main.CalendarSyncProcessor") as mock_processor,
    ):
        mock_auth.return_value.get_credentials.return_value = MagicMock()
        main(["exams", "--dry-run", "--quiet", "--prune-before", "2025-01-01"])

    _, kwargs = mock_processor.call_args
    assert kwargs["dry_run"] is True
    assert kwargs["quiet"] is True
    assert kwargs["prune_before"] == date(2025, 1, 1)
    mock_processor.return_value.process.assert_called_once()
