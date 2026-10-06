import runpy
import sys
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

import pytest
from conftest import FakeCalendarGateway, save_profile, saved

from calendar_coloring.auth import (
    CredentialsFileNotFoundError,
    LoginRequiredError,
)
from calendar_coloring.cli import main as cli
from calendar_coloring.config import Config
from calendar_coloring.ical_source import IcalFeedSource, mask_url
from calendar_coloring.profile import CalendarSettings
from calendar_coloring.reporting import NullReporter
from calendar_coloring.suggestions import suggest_color
from calendar_coloring.sync.source import GoogleCalendarSource
from calendar_coloring.targets import SyncTarget
from calendar_coloring.workflow import SyncOptions

SOURCE = [
    {
        "id": "lec00001",
        "summary": "Lezione: Didattica - CS",
        "start": {"date": "2026-09-20"},
        "end": {"date": "2026-09-21"},
    }
]


# -- argument parsing --------------------------------------------------------


def test_parse_args_defaults() -> None:
    assert cli.parse_args([]) == cli.CliArgs(options=SyncOptions())


def test_parse_args_all_options() -> None:
    args = cli.parse_args(
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
    assert args == cli.CliArgs(
        options=SyncOptions(
            target=SyncTarget.DEADLINES,
            interactive=True,
            dry_run=True,
            prune_before=date(2025, 1, 1),
        ),
        verbose=True,
        ical_url="https://example.com/feed.ics",
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["exams"], SyncOptions(target=SyncTarget.EXAMS)),
        (["lectures", "-i"], SyncOptions(SyncTarget.LECTURES, interactive=True)),
        (["-n"], SyncOptions(dry_run=True)),
    ],
)
def test_parse_args_options(argv, expected) -> None:
    assert cli.parse_args(argv).options == expected


def test_parse_args_quiet_and_source_url_alias() -> None:
    args = cli.parse_args(["-q", "--source-ical-url", "https://x/y"])
    assert args.quiet
    assert args.ical_url == "https://x/y"


@pytest.mark.parametrize(
    "argv",
    [["unknown"], ["--prune-before", "yesterday"], ["-v", "-q"]],
)
def test_parse_args_rejects_invalid_input(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(argv)
    assert exc_info.value.code == 2


def test_bad_prune_date_message(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--prune-before", "yesterday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_mask_url_hides_token() -> None:
    masked = mask_url("https://ical.example.com/12345/secret-token-abc")
    assert masked == "https://ical.example.com/<redacted>"


@pytest.mark.parametrize("url", ["not a url", "", "http://[::1"])
def test_mask_url_handles_garbage(url: str) -> None:
    assert mask_url(url) == "<redacted>"


# -- run() -------------------------------------------------------------------


def test_run_returns_zero_on_success(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    assert cli.run(SyncOptions(), config, gateway, NullReporter()) == cli.EXIT_OK
    assert saved(config, "courses") == {"CS": "3"}


def test_run_reports_missing_source(config: Config) -> None:
    reporter = MagicMock()
    code = cli.run(SyncOptions(), config, FakeCalendarGateway(), reporter)
    assert code == cli.EXIT_FAILURE
    reporter.error.assert_called_once_with("Source calendar 'Src' not found.")


def test_run_reports_ical_failure_without_touching_calendar(config: Config) -> None:
    from calendar_coloring.ical_source import ICalError

    def broken_fetch(url: str) -> str:
        raise ICalError("Could not download iCal feed.")

    gateway = FakeCalendarGateway({"Tgt": []})
    reporter = MagicMock()
    source = IcalFeedSource("https://x/y", fetch=broken_fetch)

    code = cli.run(SyncOptions(), config, gateway, reporter, source=source)

    assert code == cli.EXIT_FAILURE
    reporter.error.assert_called_once_with("Could not download iCal feed.")
    assert gateway.batches == []


def test_run_fails_when_a_mutation_fails(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    gateway.fail_event_ids = {"lec00001"}
    assert cli.run(SyncOptions(), config, gateway, NullReporter()) == cli.EXIT_FAILURE


def test_run_routes_preference_warnings_to_reporter(config: Config) -> None:
    config.profile_path.write_text("{oops")
    reporter = MagicMock()
    cli.run(SyncOptions(), config, FakeCalendarGateway({"Src": SOURCE}), reporter)
    reporter.warning.assert_called_once()
    assert "corrupted" in reporter.warning.call_args.args[0]


def test_run_describes_configuration_without_leaking_ical_token(config) -> None:
    config = replace(config, source_ical_url="https://ical.example/42/secret")
    reporter = MagicMock()
    cli.describe_run(
        SyncOptions(dry_run=True, prune_before=date(2025, 1, 1)),
        config,
        CalendarSettings(source="Src", target="Tgt"),
        reporter,
    )
    details = [c.args[0] for c in reporter.detail.call_args_list]
    assert details == [
        f"Profile: {config.profile_path}",
        "Source: iCal feed at https://ical.example/<redacted>",
        "Target: Google Calendar 'Tgt'",
        "Options: dry-run, prune-before=2025-01-01",
    ]


def test_build_source(config: Config) -> None:
    gateway = FakeCalendarGateway()
    calendars = CalendarSettings(source="Src", target="Tgt")
    google = cli.build_source(config, calendars, gateway, NullReporter())
    ical = cli.build_source(
        replace(config, source_ical_url="https://x/y"),
        calendars,
        gateway,
        NullReporter(),
    )
    assert isinstance(google, GoogleCalendarSource)
    assert google.name == "Src"
    assert isinstance(ical, IcalFeedSource)
    assert ical.url == "https://x/y"


# -- main() ------------------------------------------------------------------


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    for key in (
        "SOURCE_CALENDAR_NAME",
        "TARGET_CALENDAR_NAME",
        "CREDENTIALS_PATH",
        "SOURCE_ICAL_URL",
        "PROFILE_PATH",
        "CI",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    return tmp_path


@pytest.fixture
def fake_auth(monkeypatch) -> MagicMock:
    authenticator = MagicMock()
    monkeypatch.setattr(cli, "Authenticator", authenticator)
    return authenticator


def install_gateway(monkeypatch, gateway) -> None:
    monkeypatch.setattr(
        cli.GoogleCalendarClient,
        "from_credentials",
        classmethod(lambda cls, c: gateway),
    )


def test_main_wires_everything(isolated_env, fake_auth, monkeypatch) -> None:
    gateway = FakeCalendarGateway({"Calendar": SOURCE})
    install_gateway(monkeypatch, gateway)

    assert cli.main(["lectures"]) == cli.EXIT_OK

    kwargs = fake_auth.call_args.kwargs
    assert str(kwargs["token_path"]) == "token.json"
    assert str(kwargs["legacy_token_path"]) == "token.pickle"
    assert gateway.created == ["Calendar Colored"]
    assert saved(Config(profile_path=isolated_env / "profile.json"), "courses") == {
        "CS": suggest_color("CS").color_id
    }


def test_main_reads_the_calendars_from_the_profile(
    isolated_env, fake_auth, monkeypatch
) -> None:
    save_profile(
        Config(profile_path=isolated_env / "profile.json"),
        calendars={"source": "Uni", "target": "Uni colored"},
    )
    gateway = FakeCalendarGateway({"Uni": SOURCE})
    install_gateway(monkeypatch, gateway)

    assert cli.main(["--no-tui"]) == cli.EXIT_OK
    assert gateway.created == ["Uni colored"]


def test_env_overrides_the_profile_calendars(
    isolated_env, fake_auth, monkeypatch
) -> None:
    save_profile(
        Config(profile_path=isolated_env / "profile.json"),
        calendars={"source": "Uni", "target": "Uni colored"},
    )
    monkeypatch.setenv("SOURCE_CALENDAR_NAME", "Other")
    monkeypatch.setenv("TARGET_CALENDAR_NAME", "Other colored")
    gateway = FakeCalendarGateway({"Other": SOURCE})
    install_gateway(monkeypatch, gateway)

    assert cli.main(["--no-tui"]) == cli.EXIT_OK
    assert gateway.created == ["Other colored"]
    # Overrides are not written back into the profile.
    assert saved(Config(profile_path=isolated_env / "profile.json"), "calendars") == {
        "source": "Uni",
        "target": "Uni colored",
        "time_zone": None,
    }


def test_main_ical_flag_overrides_env(
    isolated_env, fake_auth, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("SOURCE_ICAL_URL", "https://env/feed")
    gateway = FakeCalendarGateway()
    install_gateway(monkeypatch, gateway)
    fetched: list[str] = []

    def fake_urlopen(request, timeout):
        fetched.append(request.full_url)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        )
        return response

    monkeypatch.setattr(
        "calendar_coloring.ical_source.urllib.request.urlopen", fake_urlopen
    )

    assert cli.main(["--ical", "https://cli/feed", "-v"]) == cli.EXIT_OK
    assert fetched == ["https://cli/feed"]
    out = capsys.readouterr().out
    assert "Source: iCal feed at https://cli/<redacted>" in out
    assert "feed" not in out.split("Source: iCal feed at")[1].splitlines()[0]


def test_main_reports_missing_credentials(isolated_env, fake_auth, capsys) -> None:
    fake_auth.return_value.get_credentials.side_effect = CredentialsFileNotFoundError(
        isolated_env / "credentials.json"
    )
    assert cli.main([]) == cli.EXIT_FAILURE
    assert "credentials.json' not found" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("tty", "ci", "allowed"),
    [(True, None, True), (False, None, False), (True, "true", False)],
)
def test_main_allows_browser_login_only_when_interactive(
    isolated_env, fake_auth, monkeypatch, tty, ci, allowed
) -> None:
    monkeypatch.setattr(sys, "stdin", MagicMock(isatty=lambda: tty))
    if ci:
        monkeypatch.setenv("CI", ci)
    fake_auth.return_value.get_credentials.side_effect = LoginRequiredError()

    assert cli.main([]) == cli.EXIT_FAILURE
    assert fake_auth.call_args.kwargs["allow_browser_login"] is allowed


def test_main_reports_login_required(isolated_env, fake_auth, capsys) -> None:
    fake_auth.return_value.get_credentials.side_effect = LoginRequiredError()
    assert cli.main([]) == cli.EXIT_FAILURE
    assert "Google login required" in capsys.readouterr().out


def test_main_quiet_prints_nothing_on_success(
    isolated_env, fake_auth, monkeypatch, capsys
) -> None:
    install_gateway(monkeypatch, FakeCalendarGateway({"Calendar": SOURCE}))
    assert cli.main(["-q"]) == cli.EXIT_OK
    assert capsys.readouterr().out == ""


def test_module_entry_point_shows_help(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["calendar-coloring", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("calendar_coloring", run_name="__main__")
    assert exc_info.value.code == 0
    assert "--prune-before" in capsys.readouterr().out


def test_ical_mode_never_reads_the_google_source_calendar(config: Config) -> None:
    ical = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1172587-polimi.it\n"
        "DTSTART:20260917T101500\nDTEND:20260917T121500\n"
        "SUMMARY:Lezione: Didattica - CS\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    source = IcalFeedSource("https://x/y", fetch=lambda url: ical)

    assert cli.run(SyncOptions(), config, gateway, NullReporter(), source=source) == 0

    assert gateway.listings == [("id::Tgt", False)]  # target only
    [event] = gateway.events_of("Tgt")
    assert event["id"] == "1172587polimiit284d64df"
    assert event["summary"] == "CS"


# -- terminal UI -------------------------------------------------------------


def test_parse_args_tui() -> None:
    args = cli.parse_args(["exams", "--tui", "--prune-before", "2025-01-01"])
    assert args.tui
    assert args.options == SyncOptions(
        target=SyncTarget.EXAMS, prune_before=date(2025, 1, 1)
    )


@pytest.mark.parametrize("argv", [["--tui", "-i"], ["--tui", "--dry-run"]])
def test_parse_args_rejects_tui_with_other_modes(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(argv)
    assert exc_info.value.code == 2


def test_main_tui_without_textual_fails_before_login(
    isolated_env, fake_auth, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "tui_available", lambda: False)
    assert cli.main(["--tui"]) == cli.EXIT_FAILURE
    assert "pip install 'calendar-coloring[tui]'" in capsys.readouterr().out
    fake_auth.assert_not_called()


def test_main_tui_runs_the_app(isolated_env, fake_auth, monkeypatch) -> None:
    pytest.importorskip("textual")
    from calendar_coloring.tui.app import CalendarColoringApp

    gateway = FakeCalendarGateway({"Calendar": SOURCE})
    install_gateway(monkeypatch, gateway)
    launched: list[CalendarColoringApp] = []
    monkeypatch.setattr(CalendarColoringApp, "run", lambda app: launched.append(app))

    assert cli.main(["lectures", "--tui"]) == cli.EXIT_OK

    [app] = launched
    assert app.options == SyncOptions(target=SyncTarget.LECTURES)
    assert app.calendars.target == "Calendar Colored"
    assert isinstance(app.source, GoogleCalendarSource)
    assert gateway.batches == []


def test_tui_available_matches_installed_textual() -> None:
    import importlib.util

    assert cli.tui_available() is (importlib.util.find_spec("textual") is not None)


@pytest.mark.parametrize(
    ("argv", "interactive_shell", "expected"),
    [
        ([], True, True),
        (["exams"], True, True),
        ([], False, False),  # cron, CI, pipes
        (["--no-tui"], True, False),
        (["-i"], True, False),
        (["--dry-run"], True, False),
        (["--tui"], False, True),  # explicit request wins over the shell check
    ],
)
def test_tui_is_the_default_in_a_terminal(
    argv, interactive_shell, expected, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "_is_interactive_shell", lambda: interactive_shell)
    assert cli.parse_args(argv).tui is expected


def test_parse_args_rejects_tui_with_no_tui() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args(["--tui", "--no-tui"])
    assert exc_info.value.code == 2


def test_default_tui_without_textual_falls_back_to_plain_sync(
    isolated_env, fake_auth, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "_is_interactive_shell", lambda: True)
    monkeypatch.setattr(cli, "tui_available", lambda: False)
    gateway = FakeCalendarGateway({"Calendar": SOURCE})
    install_gateway(monkeypatch, gateway)

    assert cli.main([]) == cli.EXIT_OK

    assert "Running a plain sync instead" in capsys.readouterr().out
    assert gateway.created == ["Calendar Colored"]


def test_run_tui_builds_the_calendar_setup(config: Config, monkeypatch) -> None:
    pytest.importorskip("textual")
    from calendar_coloring.tui.app import CalendarColoringApp
    from calendar_coloring.tui.setup import Role

    apps: list[CalendarColoringApp] = []
    monkeypatch.setattr(CalendarColoringApp, "run", lambda self: apps.append(self))
    save_profile(config, calendars={"source": "Uni", "target": "Mine"})
    gateway = FakeCalendarGateway()

    overridden = replace(config, source_calendar_name=None)
    assert cli.run_tui(SyncOptions(), overridden, gateway) == cli.EXIT_OK
    setup = apps[-1].setup
    assert setup.calendars == CalendarSettings(source="Uni", target="Tgt")
    assert setup.overrides == {Role.TARGET: "TARGET_CALENDAR_NAME"}
    assert setup.fixed_source is None
    source = setup.source_for("Other")
    assert isinstance(source, GoogleCalendarSource)
    assert source.name == "Other"

    ical = replace(config, source_ical_url="https://ical.example/42/secret")
    cli.run_tui(SyncOptions(), ical, gateway)
    setup = apps[-1].setup
    assert setup.fixed_source == "iCal feed at https://ical.example/<redacted>"
    assert isinstance(setup.source_for("Other"), IcalFeedSource)
