import argparse
import os
import sys
from datetime import date
from urllib.parse import urlsplit

from dotenv import load_dotenv

from auth import Authenticator
from calendar_client import CalendarClient
from colors import Colors
from strategies import (
    CompositeColoringStrategy,
    DeadlineColoringStrategy,
    ExamColoringStrategy,
    LectureColoringStrategy,
)
from sync_processor import CalendarSyncProcessor


def mask_url(url: str) -> str:
    """Hides credentials/tokens in a URL so it is safe to print in logs."""
    try:
        parts = urlsplit(url)
        if not parts.netloc:
            return "<redacted>"
        return f"{parts.scheme}://{parts.netloc}/<redacted>"
    except ValueError:
        return "<redacted>"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync and color Google Calendar events."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["all", "exams", "lectures", "deadlines"],
        help="Specify what to process (all, exams, lectures, or deadlines). Default: all.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging to see exactly what is happening to each event.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress informational output (errors are still shown).",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Ask interactively if you are subscribed to each exam or pick lecture colors.",
    )
    parser.add_argument(
        "--ical",
        "--source-ical-url",
        dest="ical_url",
        default=None,
        help="Sync directly from a Polimi iCal feed URL instead of a Google "
        "source calendar. Overrides SOURCE_CALENDAR_NAME and the "
        "SOURCE_ICAL_URL env var.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and report the operations without applying any changes.",
    )
    parser.add_argument(
        "--prune-before",
        default=None,
        metavar="YYYY-MM-DD",
        help="Also delete managed target events starting before this date, "
        "even if still present in the source. Useful to drop past semesters.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    load_dotenv()

    source_name = os.getenv("SOURCE_CALENDAR_NAME", "Polimi Calendar")
    target_name = os.getenv("TARGET_CALENDAR_NAME", "Polimi Calendar Colored")
    credentials_path = os.getenv("CREDENTIALS_PATH", "credentials.json")
    ical_url = args.ical_url or os.getenv("SOURCE_ICAL_URL") or None

    prune_before: date | None = None
    if args.prune_before:
        try:
            prune_before = date.fromisoformat(args.prune_before)
        except ValueError:
            print(
                f"{Colors.FAIL}Error: --prune-before must be YYYY-MM-DD, "
                f"got '{args.prune_before}'.{Colors.ENDC}"
            )
            sys.exit(2)

    if args.verbose and not args.quiet:
        if ical_url:
            print(f"Source: iCal feed at {mask_url(ical_url)}")
        else:
            print(f"Source: Google Calendar '{source_name}'")
        print(f"Target: Google Calendar '{target_name}'")
        flags = []
        if args.dry_run:
            flags.append("dry-run")
        if prune_before:
            flags.append(f"prune-before={prune_before}")
        if flags:
            print(f"Options: {', '.join(flags)}")

    strategies_to_use = []
    if args.target in ["all", "exams"]:
        strategies_to_use.append(ExamColoringStrategy(interactive=args.interactive))
    if args.target in ["all", "lectures"]:
        strategies_to_use.append(LectureColoringStrategy(interactive=args.interactive))
    if args.target in ["all", "deadlines"]:
        strategies_to_use.append(DeadlineColoringStrategy(interactive=args.interactive))

    authenticator = Authenticator(credentials_path=credentials_path)
    try:
        creds = authenticator.get_credentials()
    except FileNotFoundError:
        print(f"{Colors.FAIL}Error: '{credentials_path}' not found.{Colors.ENDC}")
        sys.exit(1)

    client = CalendarClient(credentials=creds)
    strategy = CompositeColoringStrategy(strategies_to_use)

    processor = CalendarSyncProcessor(
        client=client,
        strategy=strategy,
        source_name=source_name,
        target_name=target_name,
        verbose=args.verbose,
        source_ical_url=ical_url,
        dry_run=args.dry_run,
        quiet=args.quiet,
        prune_before=prune_before,
    )
    processor.process()


if __name__ == "__main__":
    main()
