import argparse
import os
import sys

from dotenv import load_dotenv

from auth import Authenticator
from calendar_client import CalendarClient
from colors import Colors
from strategies import (
    CompositeColoringStrategy,
    ExamColoringStrategy,
    LectureColoringStrategy,
)
from sync_processor import CalendarSyncProcessor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync and color Google Calendar events."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["all", "exams", "lectures"],
        help="Specify what to process (all, exams, or lectures). Default: all.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging to see exactly what is happening to each event.",
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
    args = parser.parse_args()

    load_dotenv()

    source_name = os.getenv("SOURCE_CALENDAR_NAME", "Polimi Calendar")
    target_name = os.getenv("TARGET_CALENDAR_NAME", "Polimi Calendar Colored")
    credentials_path = os.getenv("CREDENTIALS_PATH", "credentials.json")
    ical_url = args.ical_url or os.getenv("SOURCE_ICAL_URL") or None

    strategies_to_use = []
    if args.target in ["all", "exams"]:
        strategies_to_use.append(ExamColoringStrategy(interactive=args.interactive))
    if args.target in ["all", "lectures"]:
        strategies_to_use.append(LectureColoringStrategy(interactive=args.interactive))

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
    )
    processor.process()


if __name__ == "__main__":
    main()
