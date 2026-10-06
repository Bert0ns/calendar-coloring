# Polimi Calendar Coloring Sync

This tool connects to your Google Calendar and synchronizes events from a read-only Polimi iCal subscription into a new, fully customizable Google Calendar. During the synchronization, it automatically color-codes your exams based on your enrollment status!

### Features

- **Intelligent Synchronization**: Safely copies events from a source calendar (or directly from your Polimi iCal feed) to a target calendar, updating only what has changed to minimize API calls and avoid rate limits. Events you add yourself to the target calendar are never touched.
- **Auto-Coloring Exams**: Automatically colors exams **Red** if you are subscribed ("Iscritto") and **Grey** if you are not ("Non iscritto"). Once you are subscribed to one session of an exam, the other sessions of the same exam are greyed out.
- **Auto-Coloring Lectures & Deadlines**: Automatically assigns unique, consistent colors to different courses ("Lezione: Didattica - [Course Name]") and deadlines ("Scadenza: ...").
- **Automatic Title Cleanup**: Automatically strips the boilerplate `"Lezione: Didattica - "` prefix from lecture titles in the target calendar, leaving only the clean course name.
- **Interactive Customization**: Run with `-i` to review your exam subscriptions and pick colors for exams, courses and deadlines. Your saved choices are offered as defaults (press Enter to keep them).
- **Dry Run**: Run with `--dry-run` to preview what would change without touching your calendar or your saved preferences.
- **Persistent Memory**: Saves your choices to `course_colors.json`, `exam_states.json` and `deadline_colors.json` so your calendar stays perfectly coordinated across future automated syncs.
- **Environment Configuration**: Easily configure calendar names, the iCal feed and file paths using a `.env` file.
- **Beautiful Terminal Output**: Features a concise, color-coded ANSI terminal interface so you know exactly what is happening.

---

## Prerequisites

1. **Python 3.10+**: Make sure Python 3.10 or newer is installed on your system.
2. **Google Cloud Account**: You need a Google Cloud account to generate API credentials.

## Setup Instructions

### 1. Configure Google Cloud Credentials

Because this tool accesses your personal Google Calendar, you must create your own API credentials:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a **New Project**.
3. In the sidebar, navigate to **APIs & Services** > **Library** and enable the **Google Calendar API**.
4. Go to **APIs & Services** > **OAuth consent screen**:
   - Choose **External** user type.
   - Fill in the required app details (name, email).
   - **Important**: Scroll down to the **Test users** section, click **+ ADD USERS**, and add your own Google email address. Without this, you will get a `403 Access Denied` error during login.
5. Go to **APIs & Services** > **Credentials**:
   - Click **+ CREATE CREDENTIALS** > **OAuth client ID**.
   - Select **Desktop app** as the application type.
   - Click Create, then download the JSON file.
6. Rename the downloaded file to `credentials.json` and place it in the same directory as this README.

> **Note**: while the OAuth app is in _Testing_ mode, Google expires the login (refresh token) after 7 days. When that happens, simply run the tool again locally to log in.

### 2. Configure Environment Variables

Copy the provided `.env.example` file to create your own `.env` file:

```bash
cp .env.example .env
```

Inside `.env`, you can customize the names of your source and target calendars, your iCal feed URL and, optionally, where credentials, token and preferences are stored.

### 3. Install

Open a terminal in the project directory and run:

```bash
pip install .
```

This installs the `polimi-calendar` command (you can also use `python -m polimi_calendar_coloring`).

---

## Usage

You can sync your entire calendar at once (**exams, lectures and deadlines**), or specify an individual target.

### Basic Commands (Auto-Sync)

To silently sync your calendar using automated rules and previously saved preferences:

```bash
# Sync exams, lectures and deadlines (default)
polimi-calendar

# Or explicitly specify a target
polimi-calendar all
polimi-calendar exams
polimi-calendar lectures
polimi-calendar deadlines
```

_(Running the sync will read from `course_colors.json`, `exam_states.json` and `deadline_colors.json` to preserve your past color choices)._

The first run opens your browser to log in with Google; the login is then cached in `token.json`. Without a browser (e.g. WSL or SSH), the login URL is printed so you can open it manually.

### Interactive Commands (Customization)

To customize or review your preferences, add the `-i` flag:

```bash
polimi-calendar -i
polimi-calendar exams -i
polimi-calendar lectures -i
polimi-calendar deadlines -i
```

- **When running `exams -i`**: You are asked about each exam, with your current saved subscription status and color as defaults (press Enter to keep). Dates of the same exam that have no saved choice yet reuse your first answer instead of asking again. You can pick custom colors or update subscriptions.
- **When running `lectures -i`**: You go through your courses, showing your existing color as default (or a deterministic suggestion for new courses). Press Enter to keep, or enter a number 1-11 to customize.
- **When running `deadlines -i`**: Same per-title color picker for deadlines such as `Scadenza: Esame di laurea`.

### Syncing directly from a Polimi iCal URL

Instead of subscribing to the Polimi calendar inside Google Calendar, you can
pull events straight from your personal iCal feed (e.g. from the Polimi app):

```bash
polimi-calendar --ical "https://ical-polimiapp.polimi.it/<your-id>/<your-token>"
```

Or set it once in `.env` so every run uses it (takes precedence over
`SOURCE_CALENDAR_NAME`):

```bash
SOURCE_ICAL_URL="https://ical-polimiapp.polimi.it/<your-id>/<your-token>"
```

> ⚠️ **Treat the iCal URL like a password**: anyone with the link can read your
> timetable. Keep it in `.env` (gitignored) or a `--ical` argument — never
> commit it. The active source is printed (with the token redacted) in `-v`
> mode so you can confirm which source is used.

Recurring iCal events (`RRULE`/`EXDATE`) are passed through to Google Calendar,
which expands them natively. Events are also matched by their `CATEGORIES`
(`Esame`/`Lezione`/`Scadenza`) when the usual title prefixes are missing.

This is also handy for GitHub Actions: just add `SOURCE_ICAL_URL` as a
repository secret — no Google source-calendar subscription needed, only the
target calendar where colored events are written.

### Safe operations: dry-run, pruning and quiet mode

```bash
# Preview operations without changing the calendar or saved preferences
polimi-calendar --dry-run

# Also delete managed target events starting before a date (drops past semesters)
polimi-calendar --prune-before 2025-01-01

# Suppress informational output (for cron/scheduled runs)
polimi-calendar -q
```

Flags compose freely, e.g. `polimi-calendar --ical <url> --dry-run -v`. Combine `-i` with `--dry-run` to try out choices without saving them. The command exits with a non-zero status if any calendar operation fails.

### Verbose Logging

To see a detailed breakdown of decisions and batch operations, use the `-v` flag:

```bash
polimi-calendar -v
```

---

## Important Note

Because the tool creates a _copy_ of your events to color them, **you should hide the original calendar** in your Google Calendar web interface or mobile app (only needed in Google-source mode — with `--ical` there is no source subscription to hide). Otherwise, you will see duplicates of every event! You can do this by unchecking the box next to the original "Polimi <student_id>" calendar in the sidebar.

## Running in the Cloud (GitHub Actions)

This repository includes a GitHub Actions workflow (`.github/workflows/manual_sync.yml`) that syncs **automatically every Monday at 06:00 UTC** and can also be triggered manually from the Actions tab without running anything on your laptop!

### Setup Instructions for GitHub Actions

Because your API credentials are kept highly secure and ignored by Git, you must provide them to GitHub as Repository Secrets:

1. **Log in locally once** so that `token.json` is created (e.g. run `polimi-calendar --dry-run`).
2. **Add Secrets**: Go to your GitHub repository **Settings** -> **Secrets and variables** -> **Actions**. Click **New repository secret** and add:
   - `GCP_CREDENTIALS_JSON`: Paste the raw text contents of your `credentials.json` file.
   - `GCP_TOKEN_JSON`: Paste the raw text contents of your `token.json` file.
   - `SOURCE_CALENDAR_NAME`: custom source calendar name
   - `TARGET_CALENDAR_NAME`: custom target calendar name
   - `SOURCE_ICAL_URL`: your personal Polimi iCal feed URL (optional; takes precedence over the source calendar)

The workflow also accepts `target`, `verbose` and `dry_run` inputs when triggered manually. The cloud run cannot open a browser: if the login has expired (see the note on _Testing_ mode above), the workflow fails with a clear message. Run the tool locally to log in again and update the `GCP_TOKEN_JSON` secret.

> Upgrading from an older version? `token.pickle` is migrated to `token.json` automatically, and the old `GCP_TOKEN_PICKLE_B64` secret is still accepted.

### Using your custom colors in the cloud

By default, your custom color choices are in `course_colors.json`, `exam_states.json` and `deadline_colors.json`. After each cloud run, the workflow commits any updated state files back to the repository, so local and cloud runs stay in sync.

---

## Development

```bash
pip install -e ".[dev]"
pytest          # tests (incl. regression tests against the previous implementation)
mypy            # strict type checking
ruff check .    # linting
black .         # formatting
```

## Architecture

The code is split into three phases (**discover → preferences → plan/apply**) and follows SOLID principles: the core never prints or prompts, and depends on small interfaces (ports) implemented by adapters.

```
src/polimi_calendar_coloring/
├── palette.py         # GoogleColor: single source of truth for the 11 colors
├── events.py          # Polimi event parsing (prefixes, iCal categories, title cleanup)
├── catalog.py         # Discover: courses, exam sessions and deadlines to sync
├── preferences.py     # Preferences model + JSON repository (atomic writes)
├── suggestions.py     # Pure rules for default colors / subscriptions
├── resolution.py      # Auto-fill missing preferences (non-interactive mode)
├── strategies.py      # Pure coloring strategies: event + preferences -> color
├── sync/
│   ├── source.py      # EventSource port + Google calendar source
│   ├── planner.py     # Pure diff: source + target events -> SyncPlan
│   ├── models.py      # Mutation, SyncPlan, SyncResult value objects
│   ├── gateway.py     # CalendarGateway port
│   └── service.py     # Target-calendar I/O around the planner (plan / apply)
├── workflow.py        # Use case orchestrating the three phases
├── reporting.py       # Reporter port
├── ical_source.py     # iCal feed source (stdlib parser)
├── google_client.py   # Google Calendar API adapter (batch requests + retries)
├── auth.py            # Google OAuth (Desktop flow, token.json)
├── config.py          # Configuration from environment variables
└── cli/
    ├── main.py        # Argument parsing and wiring of concrete adapters
    ├── prompts.py     # Interactive preference editor (-i)
    ├── console.py     # Colored console reporter
    └── ansi.py        # ANSI styling helpers
```
