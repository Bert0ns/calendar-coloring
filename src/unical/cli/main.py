"""Command line entry point: wires concrete adapters into the workflow."""

from __future__ import annotations

import argparse
import functools
import importlib.util
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from unical.auth import (
    Authenticator,
    CredentialsFileNotFoundError,
    LoginRequiredError,
)
from unical.cli.console import ConsoleReporter
from unical.cli.prompts import InteractivePreferenceEditor
from unical.config import Config
from unical.google_client import GoogleCalendarClient
from unical.ical_source import IcalFeedSource, mask_url
from unical.profile import CalendarSettings, JsonProfileRepository
from unical.reporting import Reporter
from unical.sync.gateway import CalendarGateway
from unical.sync.service import SyncService
from unical.sync.source import (
    EventSource,
    GoogleCalendarSource,
    SourceError,
)
from unical.targets import SyncTarget
from unical.workflow import (
    PreferenceEditor,
    SyncOptions,
    SyncWorkflow,
)

EXIT_OK = 0
EXIT_FAILURE = 1


@dataclass(frozen=True)
class CliArgs:
    options: SyncOptions
    verbose: bool = False
    quiet: bool = False
    ical_url: str | None = None
    tui: bool = False
    tui_requested: bool = False
    """True when --tui was typed, as opposed to the TUI being the default."""


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got '{value}'") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unical",
        description="Sync and color Google Calendar events.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=SyncTarget.ALL.value,
        choices=[t.value for t in SyncTarget],
        help="Specify what to process (all, exams, lectures, or deadlines). "
        "Default: all.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging to see exactly what is happening to each event.",
    )
    output.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress informational output (warnings and errors are still shown).",
    )
    editor = parser.add_mutually_exclusive_group()
    editor.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Ask interactively about exam subscriptions and pick colors.",
    )
    editor.add_argument(
        "--tui",
        action="store_true",
        help="Open the terminal UI to edit preferences, preview and apply the "
        "sync. This is the default when run from a terminal. Needs the 'tui' "
        "extra: pip install '.[tui]'.",
    )
    editor.add_argument(
        "--no-tui",
        action="store_true",
        help="Run a plain, non-interactive sync instead of opening the terminal UI.",
    )
    parser.add_argument(
        "--ical",
        "--source-ical-url",
        dest="ical_url",
        default=None,
        metavar="URL",
        help="Sync directly from an iCal feed URL instead of a Google "
        "source calendar. Overrides SOURCE_CALENDAR_NAME and SOURCE_ICAL_URL.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying the calendar or saved "
        "preferences.",
    )
    parser.add_argument(
        "--prune-before",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Also delete managed target events starting before this date, even "
        "if still present in the source. Useful to drop past semesters.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tui and args.dry_run:
        parser.error("--tui always previews before applying: drop --dry-run")
    # The terminal UI is the default; options that only make sense for a plain
    # sync (-i, -n, --no-tui) switch it off, and so does a non-interactive shell.
    tui = args.tui or not (
        args.no_tui or args.interactive or args.dry_run or not _is_interactive_shell()
    )
    return CliArgs(
        options=SyncOptions(
            target=SyncTarget(args.target),
            interactive=args.interactive,
            dry_run=args.dry_run,
            prune_before=args.prune_before,
        ),
        verbose=args.verbose,
        quiet=args.quiet,
        ical_url=args.ical_url,
        tui=tui,
        tui_requested=args.tui,
    )


def calendars_to_sync(config: Config) -> CalendarSettings:
    """The profile's calendars, with the overrides of the configuration.

    Read quietly: the workflow reports problems with the profile when it loads it.
    """
    return config.calendars(JsonProfileRepository(config.profile_path).load().calendars)


def build_source(
    config: Config,
    calendars: CalendarSettings,
    gateway: CalendarGateway,
    reporter: Reporter,
) -> EventSource:
    if config.source_ical_url:
        return IcalFeedSource(config.source_ical_url, on_warning=reporter.warning)
    return GoogleCalendarSource(gateway, calendars.source)


def describe_run(
    options: SyncOptions,
    config: Config,
    calendars: CalendarSettings,
    reporter: Reporter,
) -> None:
    reporter.detail(f"Profile: {config.profile_path}")
    if config.source_ical_url:
        reporter.detail(f"Source: iCal feed at {mask_url(config.source_ical_url)}")
    else:
        reporter.detail(f"Source: Google Calendar '{calendars.source}'")
    reporter.detail(f"Target: Google Calendar '{calendars.target}'")
    flags = []
    if options.dry_run:
        flags.append("dry-run")
    if options.prune_before:
        flags.append(f"prune-before={options.prune_before}")
    if flags:
        reporter.detail(f"Options: {', '.join(flags)}")


def build_workflow(
    config: Config,
    gateway: CalendarGateway,
    reporter: Reporter,
    editor: PreferenceEditor | None = None,
) -> SyncWorkflow:
    repository = JsonProfileRepository(config.profile_path, on_warning=reporter.warning)
    return SyncWorkflow(SyncService(gateway), repository, reporter, editor=editor)


def run(
    options: SyncOptions,
    config: Config,
    gateway: CalendarGateway,
    reporter: Reporter,
    editor: PreferenceEditor | None = None,
    source: EventSource | None = None,
) -> int:
    """Runs the sync with already-built adapters. Returns the process exit code."""
    workflow = build_workflow(
        config, gateway, reporter, editor or InteractivePreferenceEditor()
    )
    calendars = calendars_to_sync(config)
    describe_run(options, config, calendars, reporter)
    try:
        outcome = workflow.run(
            options,
            source or build_source(config, calendars, gateway, reporter),
            calendars.target,
        )
    except SourceError as exc:
        reporter.error(str(exc))
        return EXIT_FAILURE
    return EXIT_OK if outcome.succeeded else EXIT_FAILURE


def run_tui(
    options: SyncOptions,
    config: Config,
    gateway: CalendarGateway,
) -> int:
    """Runs the terminal UI with already-built adapters."""
    from unical.tui.app import TuiReporter, UnicalApp
    from unical.tui.setup import CalendarSetup, Role

    reporter = TuiReporter()
    calendars = calendars_to_sync(config)

    def source_for(name: str) -> EventSource:
        return build_source(config, replace(calendars, source=name), gateway, reporter)

    overrides = {
        role: variable
        for role, variable, value in (
            (Role.SOURCE, "SOURCE_CALENDAR_NAME", config.source_calendar_name),
            (Role.TARGET, "TARGET_CALENDAR_NAME", config.target_calendar_name),
        )
        if value
    }
    setup = CalendarSetup(
        calendars=calendars,
        source_for=source_for,
        fixed_source=(
            f"iCal feed at {mask_url(config.source_ical_url)}"
            if config.source_ical_url
            else None
        ),
        overrides=overrides,
        first_run=not config.profile_path.exists(),
    )
    app = UnicalApp(build_workflow(config, gateway, reporter), setup, options, reporter)
    app.run()
    return EXIT_OK


def credentials_help(path: Path) -> list[str]:
    """How to get the OAuth client file, for the first run."""
    return [
        "The tool logs in to Google Calendar with your own OAuth client. To create it:",
        "  1. Open https://console.cloud.google.com/ and create a new project.",
        "  2. APIs & Services → Library: enable the Google Calendar API.",
        "  3. APIs & Services → OAuth consent screen: choose External, and add",
        "     your Google address under Test users.",
        "  4. APIs & Services → Credentials → Create credentials → OAuth client ID:",
        "     choose Desktop app and download the JSON file.",
        f"  5. Save it as '{path}' (or set CREDENTIALS_PATH), then run again.",
        "Details: https://github.com/Bert0ns/uni-calendar-coloring#setup",
    ]


def tui_available() -> bool:
    return importlib.util.find_spec("textual") is not None


def _is_interactive_shell() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.getenv("CI") != "true"


def _can_open_browser_login() -> bool:
    return sys.stdin.isatty() and os.getenv("CI") != "true"


def main(argv: Sequence[str] | None = None) -> int:
    cli = parse_args(argv)
    load_dotenv()
    config = Config.from_env(os.environ)
    if cli.ical_url:
        config = replace(config, source_ical_url=cli.ical_url)

    # Flush every line so progress is visible when output is piped (e.g. CI logs).
    reporter = ConsoleReporter(
        verbose=cli.verbose,
        quiet=cli.quiet,
        write=functools.partial(print, flush=True),
    )

    tui = cli.tui
    if tui and not cli.tui_requested and not tui_available():
        reporter.warning(
            "Install the optional terminal UI with: pip install "
            "'uni-calendar-coloring[tui]'. Running a plain sync instead."
        )
        tui = False
    if tui and not tui_available():
        reporter.error(
            "The terminal UI needs the optional 'textual' dependency. "
            "Install it with: pip install 'uni-calendar-coloring[tui]' "
            "(or pip install '.[tui]' from the project directory)."
        )
        return EXIT_FAILURE

    authenticator = Authenticator(
        credentials_path=config.credentials_path,
        token_path=config.token_path,
        legacy_token_path=config.legacy_token_path,
        on_info=reporter.info,
        allow_browser_login=_can_open_browser_login(),
    )
    try:
        credentials = authenticator.get_credentials()
    except (CredentialsFileNotFoundError, LoginRequiredError) as exc:
        reporter.error(str(exc))
        if isinstance(exc, CredentialsFileNotFoundError):
            for line in credentials_help(exc.path):
                reporter.info(line)
        return EXIT_FAILURE

    gateway = GoogleCalendarClient.from_credentials(credentials)
    if tui:
        return run_tui(cli.options, config, gateway)
    return run(cli.options, config, gateway, reporter)
