"""Command line entry point: wires concrete adapters into the workflow."""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import GoogleAuthError

from unical import __version__
from unical.auth import (
    Authenticator,
    CredentialsFileNotFoundError,
    InvalidCredentialsError,
    LoginRequiredError,
    import_credentials,
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
from unical.version_check import (
    AsyncUpdateChecker,
    check_for_updates,
    default_version_cache_path,
    is_newer_version,
    read_version_cache,
    should_check_for_updates,
)
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
    check_update: bool = True
    check_update_only: bool = False


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD, got '{value}'") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unical",
        description="Sync and color Google Calendar events.\n"
        "Use 'unical auth' to manage credentials and login status.",
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
        "sync. This is the default when run from a terminal.",
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
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Do not check for newer versions of unical.",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check for a newer version of unical and exit.",
    )
    return parser


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unical auth",
        description="Manage Google OAuth credentials and authentication.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    import_parser = subparsers.add_parser(
        "import",
        help="Import a credentials JSON file into the user config directory.",
        description="Import and validate a Google OAuth client credentials JSON file.",
    )
    import_parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to downloaded credentials JSON file (or drag & drop).",
    )
    import_parser.add_argument(
        "-m",
        "--move",
        action="store_true",
        help="Move the file instead of copying it.",
    )
    import_parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help="Custom destination path (defaults to user config credentials.json).",
    )

    subparsers.add_parser(
        "status",
        help="Show credentials and authentication status.",
        description="Display information about configured credentials, token cache, and profile.",
    )

    subparsers.add_parser(
        "login",
        help="Log in with Google via browser and cache token.",
        description="Authenticate with Google Calendar using the configured credentials.",
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
        check_update=not args.no_update_check,
        check_update_only=args.check_update,
    )


def calendars_to_sync(config: Config) -> CalendarSettings:
    """The profile's calendars, with the overrides of the configuration.

    Read quietly: the workflow reports problems with the profile when it loads it."""
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
    check_update: bool = True,
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
    app = UnicalApp(
        build_workflow(config, gateway, reporter),
        setup,
        options,
        reporter,
        check_update=check_update,
    )
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
        f"  5. Save it as '{path}' (or run 'unical auth import /path/to/downloaded-credentials.json').",
        "Details: https://github.com/Bert0ns/uni-calendar-coloring#setup",
    ]


def run_cli_credentials_onboarding(
    destination: Path,
    reporter: Reporter,
    prompt_fn: Callable[[str], str] = input,
) -> Path | None:
    """Guided interactive terminal onboarding when credentials are not found."""
    reporter.info("=" * 72)
    reporter.info("  Google Calendar Credentials Setup")
    reporter.info("=" * 72)
    reporter.info(
        "Uni Calendar Coloring requires a Google Cloud OAuth client credentials\n"
        "file (credentials.json) to connect to your Google Calendar.\n\n"
        "To get your credentials file:\n"
        "  1. Open Google Cloud Console: https://console.cloud.google.com/\n"
        "  2. Enable the Google Calendar API:\n"
        "     https://console.cloud.google.com/apis/library/calendar-json.googleapis.com\n"
        "  3. Configure OAuth consent screen (External, add yourself as Test user):\n"
        "     https://console.cloud.google.com/apis/credentials/consent\n"
        "  4. Create Credentials → OAuth client ID → Desktop app, and download the JSON:\n"
        "     https://console.cloud.google.com/apis/credentials\n"
    )
    reporter.info("=" * 72)

    while True:
        try:
            raw = prompt_fn(
                "Enter or drag-and-drop the path to your downloaded credentials JSON file\n"
                "(or press Enter to cancel): "
            )
        except (KeyboardInterrupt, EOFError):
            return None

        val = raw.strip()
        if not val:
            return None

        try:
            dest = import_credentials(val, destination=destination)
            reporter.info(f"✔ Credentials successfully imported to '{dest}'.")
            return dest
        except (FileNotFoundError, InvalidCredentialsError, OSError) as exc:
            reporter.error(str(exc))


def main_auth(
    argv: Sequence[str],
    config: Config,
    reporter: Reporter,
    prompt_fn: Callable[[str], str] = input,
) -> int:
    """Handler for 'unical auth' subcommands."""
    parser = build_auth_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return EXIT_OK

    if args.subcommand == "import":
        file_arg = args.file
        if not file_arg:
            if _is_interactive_shell() or prompt_fn is not input:
                try:
                    raw = prompt_fn(
                        "Enter or drag-and-drop the path to your credentials JSON file: "
                    )
                except (KeyboardInterrupt, EOFError):
                    return EXIT_FAILURE
                file_arg = raw.strip()
                if not file_arg:
                    reporter.info("Cancelled.")
                    return EXIT_FAILURE
            else:
                reporter.error("Error: Path to credentials file is required.")
                return EXIT_FAILURE

        try:
            dest = import_credentials(
                file_arg,
                destination=args.destination or config.credentials_path,
                move=args.move,
            )
            reporter.info(f"✔ Successfully imported credentials to '{dest}'.")
            reporter.info("Run 'unical' to connect your Google account.")
            return EXIT_OK
        except (FileNotFoundError, InvalidCredentialsError, OSError) as exc:
            reporter.error(str(exc))
            return EXIT_FAILURE

    if args.subcommand == "status":
        from unical.config import user_config_dir

        reporter.info(f"Configuration directory: {user_config_dir()}")
        reporter.info(f"Version:                 {__version__}")
        cache_ver, is_fresh = read_version_cache(default_version_cache_path())
        if cache_ver and is_newer_version(cache_ver, __version__):
            reporter.info(
                f"Update status:           Update available: {cache_ver} "
                "(run 'pip install --upgrade uni-calendar-coloring')"
            )
        elif is_fresh and cache_ver:
            reporter.info("Update status:           Up to date")
        reporter.info(f"Credentials path:        {config.credentials_path}")
        if config.credentials_path.exists():
            try:
                content = config.credentials_path.read_text(encoding="utf-8")
                data = json.loads(content)
                client_type = (
                    "installed"
                    if "installed" in data
                    else ("web" if "web" in data else "unknown")
                )
                client_info = data.get(client_type, {})
                client_id = client_info.get("client_id", "unknown")
                reporter.info(f"  Status: Configured ({client_type} app)")
                reporter.info(f"  Client ID: {client_id}")
            except Exception as exc:
                reporter.info(f"  Status: Present but invalid ({exc})")
        else:
            reporter.info("  Status: Missing (run 'unical auth import' to configure)")

        reporter.info(f"Token cache path:        {config.token_path}")
        if config.token_path.exists():
            try:
                from google.oauth2.credentials import Credentials

                creds = Credentials.from_authorized_user_file(str(config.token_path))  # type: ignore[no-untyped-call]
                if creds.valid:
                    reporter.info("  Status: Logged in (token valid)")
                elif creds.expired:
                    reporter.info("  Status: Token expired (will refresh on next run)")
                else:
                    reporter.info("  Status: Logged in")
            except Exception:
                reporter.info("  Status: Token file present (could not parse)")
        else:
            reporter.info("  Status: Not logged in (token not found)")

        reporter.info(f"Profile path:            {config.profile_path}")
        if config.profile_path.exists():
            reporter.info("  Status: Present")
        else:
            reporter.info("  Status: Not found (will be created on first setup)")

        return EXIT_OK

    if args.subcommand == "login":
        if not config.credentials_path.exists():
            if _is_interactive_shell() or prompt_fn is not input:
                imported = run_cli_credentials_onboarding(
                    config.credentials_path, reporter, prompt_fn=prompt_fn
                )
                if imported is None:
                    return EXIT_FAILURE
                config = replace(config, credentials_path=imported)
            else:
                reporter.error(
                    f"OAuth client file '{config.credentials_path}' not found."
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
            authenticator.get_credentials()
            reporter.info(
                f"✔ Successfully authenticated! Token saved to '{config.token_path}'."
            )
            return EXIT_OK
        except Exception as exc:
            reporter.error(str(exc))
            return EXIT_FAILURE

    return EXIT_OK


def tui_available() -> bool:
    return importlib.util.find_spec("textual") is not None


def _is_interactive_shell() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.getenv("CI") != "true"


def _can_open_browser_login() -> bool:
    return sys.stdin.isatty() and os.getenv("CI") != "true"


def main_version(
    argv: Sequence[str] | None = None,
    reporter: Reporter | None = None,
) -> int:
    """Handler for 'unical version' command or '--check-update'."""
    rep = reporter or ConsoleReporter(write=functools.partial(print, flush=True))
    rep.info(f"unical {__version__}")

    parser = argparse.ArgumentParser(
        prog="unical version",
        description="Display version information and check for available updates.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check online for a newer version (default: True).",
    )
    parser.add_argument(
        "--no-check",
        dest="check",
        action="store_false",
        help="Skip checking online for updates.",
    )
    args = parser.parse_args(argv or [])
    if args.check and should_check_for_updates():
        rep.info("Checking for updates...")
        result = check_for_updates(force=True, enabled=True)
        if result.has_update and result.latest_version:
            rep.update_available(result.current_version, result.latest_version)
        elif result.error:
            rep.warning(f"Could not check for updates: {result.error}")
        else:
            rep.info(f"✔ You are running the latest version of unical ({__version__}).")
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    prompt_fn: Callable[[str], str] = input,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    load_dotenv()
    config = Config.from_env(os.environ)

    if raw_argv and raw_argv[0] == "version":
        reporter = ConsoleReporter(
            verbose=False,
            quiet=False,
            write=functools.partial(print, flush=True),
        )
        return main_version(raw_argv[1:], reporter=reporter)

    if raw_argv and raw_argv[0] == "auth":
        reporter = ConsoleReporter(
            verbose=False,
            quiet=False,
            write=functools.partial(print, flush=True),
        )
        return main_auth(raw_argv[1:], config, reporter, prompt_fn=prompt_fn)

    cli = parse_args(raw_argv)
    if cli.check_update_only:
        reporter = ConsoleReporter(
            verbose=False,
            quiet=False,
            write=functools.partial(print, flush=True),
        )
        return main_version([], reporter=reporter)
    if cli.ical_url:
        config = replace(config, source_ical_url=cli.ical_url)

    # Flush every line so progress is visible when output is piped (e.g. CI logs).
    reporter = ConsoleReporter(
        verbose=cli.verbose,
        quiet=cli.quiet,
        write=functools.partial(print, flush=True),
    )

    updater: AsyncUpdateChecker | None = None
    if cli.check_update and not cli.quiet:
        updater = AsyncUpdateChecker()
        updater.start()

    tui = cli.tui
    if tui and not cli.tui_requested and not tui_available():
        reporter.warning(
            "Install the terminal UI with: pip install "
            "'uni-calendar-coloring'. Running a plain sync instead."
        )
        tui = False
    if tui and not tui_available():
        reporter.error(
            "The terminal UI needs the 'textual' dependency. "
            "Install it with: pip install 'uni-calendar-coloring' "
            "(or pip install . from the project directory)."
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
    except CredentialsFileNotFoundError as exc:
        if _is_interactive_shell() or prompt_fn is not input:
            imported: Path | None = None
            if tui and tui_available():
                from unical.tui.wizard import run_credentials_wizard

                imported = run_credentials_wizard(exc.path)
            else:
                imported = run_cli_credentials_onboarding(
                    exc.path, reporter, prompt_fn=prompt_fn
                )

            if imported is not None:
                reporter.info(f"Credentials saved to '{imported}'.")
                config = replace(config, credentials_path=imported)
                authenticator = Authenticator(
                    credentials_path=config.credentials_path,
                    token_path=config.token_path,
                    legacy_token_path=config.legacy_token_path,
                    on_info=reporter.info,
                    allow_browser_login=_can_open_browser_login(),
                )
                try:
                    credentials = authenticator.get_credentials()
                except (InvalidCredentialsError, GoogleAuthError) as auth_exc:
                    message = str(auth_exc).strip()
                    error_msg = (
                        f"Authentication failed: {message}"
                        if message
                        else "Authentication failed."
                    )
                    reporter.error(error_msg)
                    reporter.info(
                        "Run 'unical auth login' to re-authenticate, or check your credentials and internet connection."
                    )
                    return EXIT_FAILURE
                except Exception as auth_exc:
                    reporter.error(str(auth_exc))
                    return EXIT_FAILURE
            else:
                return EXIT_OK
        else:
            reporter.error(str(exc))
            for line in credentials_help(exc.path):
                reporter.info(line)
            return EXIT_FAILURE
    except LoginRequiredError as exc:
        reporter.error(str(exc))
        return EXIT_FAILURE
    except (InvalidCredentialsError, GoogleAuthError) as exc:
        message = str(exc).strip()
        error_msg = (
            f"Authentication failed: {message}" if message else "Authentication failed."
        )
        reporter.error(error_msg)
        reporter.info(
            "Run 'unical auth login' to re-authenticate, or check your credentials and internet connection."
        )
        return EXIT_FAILURE

    gateway = GoogleCalendarClient.from_credentials(credentials)
    if tui:
        return run_tui(cli.options, config, gateway, check_update=cli.check_update)
    exit_code = run(cli.options, config, gateway, reporter)
    if updater is not None:
        notice = updater.get_result(timeout=0.3)
        if notice and notice.has_update and notice.latest_version:
            reporter.update_available(notice.current_version, notice.latest_version)
    return exit_code
