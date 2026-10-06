"""Command line entry point: wires concrete adapters into the workflow."""

from __future__ import annotations

import argparse
import functools
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from dotenv import load_dotenv

from polimi_calendar_coloring.auth import (
    Authenticator,
    CredentialsFileNotFoundError,
    LoginRequiredError,
)
from polimi_calendar_coloring.cli.console import ConsoleReporter
from polimi_calendar_coloring.cli.prompts import InteractivePreferenceEditor
from polimi_calendar_coloring.config import Config
from polimi_calendar_coloring.google_client import GoogleCalendarClient
from polimi_calendar_coloring.ical_source import IcalFeedSource, mask_url
from polimi_calendar_coloring.preferences import JsonPreferencesRepository
from polimi_calendar_coloring.reporting import Reporter
from polimi_calendar_coloring.sync.gateway import CalendarGateway
from polimi_calendar_coloring.sync.service import SyncService
from polimi_calendar_coloring.sync.source import (
    EventSource,
    GoogleCalendarSource,
    SourceError,
)
from polimi_calendar_coloring.targets import SyncTarget
from polimi_calendar_coloring.workflow import (
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


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got '{value}'") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polimi-calendar",
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
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Ask interactively about exam subscriptions and pick colors.",
    )
    parser.add_argument(
        "--ical",
        "--source-ical-url",
        dest="ical_url",
        default=None,
        metavar="URL",
        help="Sync directly from a Polimi iCal feed URL instead of a Google "
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
    args = build_parser().parse_args(argv)
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
    )


def build_source(
    config: Config, gateway: CalendarGateway, reporter: Reporter
) -> EventSource:
    if config.source_ical_url:
        return IcalFeedSource(config.source_ical_url, on_warning=reporter.warning)
    return GoogleCalendarSource(gateway, config.source_calendar_name)


def describe_run(options: SyncOptions, config: Config, reporter: Reporter) -> None:
    if config.source_ical_url:
        reporter.detail(f"Source: iCal feed at {mask_url(config.source_ical_url)}")
    else:
        reporter.detail(f"Source: Google Calendar '{config.source_calendar_name}'")
    reporter.detail(f"Target: Google Calendar '{config.target_calendar_name}'")
    flags = []
    if options.dry_run:
        flags.append("dry-run")
    if options.prune_before:
        flags.append(f"prune-before={options.prune_before}")
    if flags:
        reporter.detail(f"Options: {', '.join(flags)}")


def run(
    options: SyncOptions,
    config: Config,
    gateway: CalendarGateway,
    reporter: Reporter,
    editor: PreferenceEditor | None = None,
    source: EventSource | None = None,
) -> int:
    """Runs the sync with already-built adapters. Returns the process exit code."""
    repository = JsonPreferencesRepository(
        config.course_colors_path,
        config.exam_states_path,
        config.deadline_colors_path,
        on_warning=reporter.warning,
    )
    workflow = SyncWorkflow(
        SyncService(gateway),
        repository,
        reporter,
        editor=editor or InteractivePreferenceEditor(),
    )
    describe_run(options, config, reporter)
    try:
        outcome = workflow.run(
            options,
            source or build_source(config, gateway, reporter),
            config.target_calendar_name,
        )
    except SourceError as exc:
        reporter.error(str(exc))
        return EXIT_FAILURE
    return EXIT_OK if outcome.succeeded else EXIT_FAILURE


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
        return EXIT_FAILURE

    gateway = GoogleCalendarClient.from_credentials(credentials)
    return run(cli.options, config, gateway, reporter)
