# Polimi Calendar Coloring

Turn your Politecnico di Milano timetable into a color-coded Google Calendar.

The Polimi calendar is read-only and every event in it has the same color.
This tool copies it into a Google Calendar you own and colors each event:

- **Exams**: red if you are enrolled (_Iscritto_), grey if you are not. Once you
  enroll in one session of an exam, its other sessions turn grey.
- **Lectures**: one color per course, with the `Lezione: Didattica - ` prefix
  stripped from the title.
- **Deadlines** (`Scadenza: …`): one color per deadline.

Your choices are saved in plain JSON files, so every later sync uses the same
colors. You can run it on your laptop, from a terminal UI, or on a weekly
schedule with GitHub Actions.

![The terminal UI, Exams tab](docs/tui.svg)

## Contents

- [How it works](#how-it-works)
- [Setup](#setup)
- [Usage](#usage)
  - [Sync](#sync)
  - [Terminal UI](#terminal-ui)
  - [Interactive prompts](#interactive-prompts)
  - [Use an iCal URL as the source](#use-an-ical-url-as-the-source)
  - [Command reference](#command-reference)
- [Coloring rules](#coloring-rules)
- [Configuration](#configuration)
- [Run it in the cloud with GitHub Actions](#run-it-in-the-cloud-with-github-actions)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Architecture](#architecture)

## How it works

```
Polimi calendar ──read──▶ discover ──▶ preferences ──▶ plan ──▶ apply ──write──▶ your colored calendar
(Google or iCal)          courses,      saved colors,    diff of    batched
                          exams,        rules, or        inserts/   Google API
                          deadlines     your edits       updates/   calls
                                                         deletes
```

1. **Discover**: read the source events and list the courses, exam sessions
   and deadlines they contain.
2. **Preferences**: load your saved colors and subscriptions. Anything you
   haven't chosen yet is filled in by the [coloring rules](#coloring-rules), or
   you choose it yourself in the TUI or with the `-i` prompts.
3. **Plan**: compare the source with the target calendar and work out the
   events to insert, update and delete. Unchanged events cost no API calls.
4. **Apply**: send the changes through the Google batch API, retrying
   transient failures.

The sync is safe to repeat:

- Only events the tool created are updated or deleted. They're tagged with a
  private property. Events you add to the target calendar yourself are left
  alone.
- Events removed from the source are removed from the target.
- If an event isn't covered by the current run (for example, a lecture during
  `polimi-calendar exams`), its existing color is kept.
- The target calendar is created on the first sync.

## Setup

You need **Python 3.12+** and a Google account. The tool runs under your own
Google Cloud OAuth client, so nobody else ever sees your calendar.

### 1. Create Google API credentials

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and
   create a **new project**.
2. Go to **APIs & Services → Library** and enable the **Google Calendar API**.
3. Go to **APIs & Services → OAuth consent screen**:
   - Choose the **External** user type and fill in the required fields.
   - Under **Test users**, add your own Google address. If you skip this,
     login fails with `403 access_denied`.
4. Go to **APIs & Services → Credentials → Create credentials → OAuth client
   ID**, choose **Desktop app**, then download the JSON file.
5. Save it as `credentials.json` in the project directory.

### 2. Install

```bash
git clone https://github.com/Bert0ns/polimi-calendar-coloring.git
cd polimi-calendar-coloring
pip install ".[tui]"      # or `pip install .` if you don't want the terminal UI
```

This installs the `polimi-calendar` command. `python -m polimi_calendar_coloring`
works too.

### 3. Configure

```bash
cp .env.example .env
```

In `.env`, set the name of the calendar to read and the one to write:

```bash
SOURCE_CALENDAR_NAME="Polimi 10123456"        # the Polimi calendar you subscribed to
TARGET_CALENDAR_NAME="Polimi Colored"         # created on the first sync
```

If you'd rather not subscribe to the Polimi calendar in Google, give the tool
your [iCal URL](#use-an-ical-url-as-the-source) instead. All settings are listed
under [Configuration](#configuration).

### 4. First run

```bash
polimi-calendar --dry-run
```

Your browser opens so you can log in with Google, and the login is saved to
`token.json`. Without a browser (WSL, SSH), the login URL is printed for you to
open by hand. The dry run then shows what a sync would change without touching
anything.

> [!IMPORTANT]
> The colored calendar is a **copy** of the Polimi calendar. In Google Calendar,
> hide the original Polimi calendar (untick it in the sidebar) to avoid seeing
> every event twice. If you use an iCal URL as the source, there is nothing to
> hide.

## Usage

### Sync

```bash
polimi-calendar             # exams, lectures and deadlines
polimi-calendar exams       # only (re)color exams
polimi-calendar lectures
polimi-calendar deadlines
```

The sync is non-interactive, so it also runs fine from cron or CI. Anything
new gets a color from the [coloring rules](#coloring-rules), and that color is
saved for future runs.

Useful flags:

```bash
polimi-calendar --dry-run                   # show the changes, write nothing
polimi-calendar -v                          # explain the decision for every event
polimi-calendar -q                          # only warnings and errors
polimi-calendar --prune-before 2026-02-01   # also delete synced events before this date
```

`--prune-before` is handy for dropping past semesters. Flags can be combined,
e.g. `polimi-calendar lectures --dry-run -v`. The command exits with a non-zero
status if any calendar operation fails.

### Terminal UI

```bash
polimi-calendar --tui
polimi-calendar exams --tui    # only the Exams and Sync tabs
```

The TUI needs the `tui` extra (`pip install ".[tui]"`). It opens with one tab
per kind of event:

| Tab           | What you can do                                                                                                                                                                                      |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Courses**   | Every course in the source with its color. Pick a new color from the 11 Google colors.                                                                                                               |
| **Exams**     | Every exam session with its date, what Polimi says (_Iscritto_ / _Non iscritto_), whether you're already subscribed to another date, and your subscription. Toggle the subscription or pick a color. |
| **Deadlines** | Same as Courses, for deadlines.                                                                                                                                                                      |
| **Sync**      | **Preview changes** lists the inserts, updates and deletes; expand an event to see its color, time and original title. **Apply** writes them to Google Calendar with a progress bar.                 |

The **Status** column shows where each value comes from:

- **saved**: chosen earlier and stored in the JSON files.
- **suggested**: not chosen yet, so the coloring rules decide. It's saved at
  the next sync.
- **modified**: changed in this session and not saved yet.
- **not set**: an exam with no enrollment information. Its color is left as is
  until you choose one.

| Key           | Action                                         |
| ------------- | ---------------------------------------------- |
| `Enter` / `c` | Pick a color for the selected row              |
| `Space`       | Toggle the exam subscription (Exams tab)       |
| `Ctrl+S`      | Save preferences without syncing               |
| `p`           | Preview the changes                            |
| `a`           | Apply the previewed changes                    |
| `q`           | Quit (asks again if there are unsaved changes) |

Toggling a subscription switches between the default red and grey. A custom
color you picked for the exam is kept. Applying saves your preferences, just
like a normal sync. If you edit something after a preview, preview again
before applying.

### Interactive prompts

```bash
polimi-calendar -i
polimi-calendar exams -i
```

Without the TUI you get a question-by-question flow before the sync: your
subscription to each exam session, then a color for each course and deadline.
The current choice is the default, so you can press Enter to keep it. Other
dates of the same exam with no saved choice reuse your first answer. Add `--dry-run` to
try choices without saving them.

### Use an iCal URL as the source

You can read events straight from your personal Polimi iCal feed (e.g. from the
Polimi app) instead of a Google calendar:

```bash
polimi-calendar --ical "https://ical-polimiapp.polimi.it/<id>/<token>"
```

Or set it once in `.env`, where it takes precedence over `SOURCE_CALENDAR_NAME`:

```bash
SOURCE_ICAL_URL="https://ical-polimiapp.polimi.it/<id>/<token>"
```

Recurring events (`RRULE`/`EXDATE`) are passed to Google, which expands them.
Events are classified by their `CATEGORIES` (`Lezione`, `Esame`, `Scadenza`)
when the title prefixes are missing.

> [!WARNING]
> Treat the iCal URL like a password: anyone who has it can read your
> timetable. Keep it in `.env` (which is gitignored) or in a CI secret. The tool
> never logs it; `-v` shows only the host with the token redacted.

### Command reference

```
polimi-calendar [all|exams|lectures|deadlines] [options]
```

| Option                                            | Description                                                                     |
| ------------------------------------------------- | ------------------------------------------------------------------------------- |
| `all` (default), `exams`, `lectures`, `deadlines` | Which kinds of events to color and edit.                                        |
| `--tui`                                           | Open the terminal UI. Can't be combined with `-i` or `--dry-run`.               |
| `-i`, `--interactive`                             | Ask about subscriptions and colors before syncing.                              |
| `-n`, `--dry-run`                                 | Show what would change. Nothing is written to the calendar or the JSON files.   |
| `--ical URL`                                      | Read from an iCal feed. Overrides `SOURCE_CALENDAR_NAME` and `SOURCE_ICAL_URL`. |
| `--prune-before YYYY-MM-DD`                       | Also delete synced events starting before this date.                            |
| `-v`, `--verbose`                                 | Show the decision for every event.                                              |
| `-q`, `--quiet`                                   | Show only warnings and errors.                                                  |

## Coloring rules

These rules fill in anything you haven't chosen yourself. Your saved choices
always win.

| Event                                                 | Rule                                                                                                           |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Exam, another session of an exam you're subscribed to | Not subscribed, **Graphite** (grey). Checked first.                                                            |
| Exam, description starts with `Iscritto`              | Subscribed, **Tomato** (red).                                                                                  |
| Exam, description starts with `Non iscritto`          | Not subscribed, **Graphite**.                                                                                  |
| Exam, no information                                  | Left uncolored until you choose.                                                                               |
| Lecture (`Lezione: Didattica - <course>`)             | A color derived from the course name, so it's the same on every machine. The prefix is removed from the title. |
| Deadline (`Scadenza: <name>`)                         | A color derived from the name. The title is kept as is.                                                        |

### Saved preferences

| File                   | Content                                                                  |
| ---------------------- | ------------------------------------------------------------------------ |
| `course_colors.json`   | `{"<course>": "<color id>"}`                                             |
| `exam_states.json`     | `{"<exam title> (<YYYY-MM-DD>)": {"color": "<id>", "subscribed": true}}` |
| `deadline_colors.json` | `{"<deadline>": "<color id>"}`                                           |

Color IDs are Google's: 1 Lavender, 2 Sage, 3 Grape, 4 Flamingo, 5 Banana,
6 Tangerine, 7 Peacock, 8 Graphite, 9 Blueberry, 10 Basil, 11 Tomato. You can
edit the files by hand. Invalid entries are skipped with a warning, and files
are written atomically, so an interrupted run can't corrupt them.

## Configuration

All settings come from environment variables, usually set in `.env`. Relative
paths are resolved from the directory you run the command in.

| Variable               | Default                   | Description                                       |
| ---------------------- | ------------------------- | ------------------------------------------------- |
| `SOURCE_CALENDAR_NAME` | `Polimi Calendar`         | Google calendar to read from.                     |
| `TARGET_CALENDAR_NAME` | `Polimi Calendar Colored` | Google calendar to write to (created if missing). |
| `SOURCE_ICAL_URL`      | _(unset)_                 | Read from this iCal feed instead of a calendar.   |
| `CREDENTIALS_PATH`     | `credentials.json`        | OAuth client downloaded from Google Cloud.        |
| `TOKEN_PATH`           | `token.json`              | Cached Google login.                              |
| `COURSE_COLORS_PATH`   | `course_colors.json`      | Saved course colors.                              |
| `EXAM_STATES_PATH`     | `exam_states.json`        | Saved exam subscriptions and colors.              |
| `DEADLINE_COLORS_PATH` | `deadline_colors.json`    | Saved deadline colors.                            |

## Run it in the cloud with GitHub Actions

The repository includes a workflow,
[`.github/workflows/manual_sync.yml`](.github/workflows/manual_sync.yml), that
syncs **every Monday at 06:00 UTC**. You can also run it from the **Actions**
tab and choose the target, verbose output, or a dry run.

1. Log in locally once so that `token.json` exists, e.g. with
   `polimi-calendar --dry-run`.
2. In your fork, go to **Settings → Secrets and variables → Actions** and add
   these repository secrets:

   | Secret                 | Value                                                       |
   | ---------------------- | ----------------------------------------------------------- |
   | `GCP_CREDENTIALS_JSON` | Contents of `credentials.json`.                             |
   | `GCP_TOKEN_JSON`       | Contents of `token.json`.                                   |
   | `SOURCE_CALENDAR_NAME` | Optional, defaults to `Polimi`.                             |
   | `TARGET_CALENDAR_NAME` | Optional, defaults to `Polimi Colored`.                     |
   | `SOURCE_ICAL_URL`      | Optional. If set, it's used instead of the source calendar. |

After each run, the workflow commits the updated preference files back to the
repository, so colors you pick locally (and push) and colors the cloud run
assigns stay in sync.

> [!NOTE]
> While your OAuth app is in **Testing** mode, Google expires the login after
> 7 days. The cloud run can't open a browser, so it then fails with a "login
> required" message. Run the tool locally to log in again and update the
> `GCP_TOKEN_JSON` secret.

## Troubleshooting

| Problem                                                   | Fix                                                                                                          |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `403 access_denied` when logging in                       | Add your Google address as a **Test user** on the OAuth consent screen.                                      |
| `OAuth client file 'credentials.json' not found`          | Download the Desktop OAuth client JSON and save it as `credentials.json`, or point `CREDENTIALS_PATH` to it. |
| `Source calendar '…' not found`                           | `SOURCE_CALENDAR_NAME` must match the calendar name exactly as shown in Google Calendar.                     |
| Every event shows up twice                                | Hide the original Polimi calendar in Google Calendar.                                                        |
| `Could not download iCal feed`                            | The iCal URL may have expired or been revoked. Generate a new one.                                           |
| `The terminal UI needs the optional 'textual' dependency` | Run `pip install ".[tui]"`.                                                                                  |
| Login keeps expiring after a week                         | That's Google's Testing mode: log in again locally (and update `GCP_TOKEN_JSON` if you use GitHub Actions).  |

> Upgrading from an older version? `token.pickle` is migrated to `token.json`
> automatically, and the old `GCP_TOKEN_PICKLE_B64` secret is still accepted.

## Development

```bash
pip install -e ".[dev,tui]"
pytest          # unit, golden regression and Textual pilot tests
mypy            # strict type checking
ruff check .    # linting
black .         # formatting
pre-commit install
```

CI runs all of the above on Python 3.12 and 3.14 and requires 95% test
coverage.

## Architecture

The core never prints, prompts or touches the network directly. It depends on
small interfaces (ports) that the adapters implement, so the CLI, the
interactive prompts and the TUI are all thin frontends over the same
discover → preferences → plan → apply phases.

```
src/polimi_calendar_coloring/
├── palette.py         # GoogleColor: the 11 Google colors (id, name, RGB)
├── events.py          # Polimi event parsing: title prefixes, iCal categories, enrollment
├── catalog.py         # Discover: courses, exam sessions and deadlines in the source
├── preferences.py     # Preferences model + JSON repository (atomic writes)
├── suggestions.py     # Pure rules for default colors and subscriptions
├── resolution.py      # Fill in preferences the user never chose
├── strategies.py      # Pure lookups: event + preferences → color
├── workflow.py        # Use case: load, preview/plan, apply (and run = all at once)
├── reporting.py       # Reporter port (progress and messages)
├── sync/
│   ├── source.py      # EventSource port + Google calendar source
│   ├── planner.py     # Pure diff: source + target events → SyncPlan
│   ├── models.py      # Mutation, SyncPlan, SyncResult value objects
│   ├── gateway.py     # CalendarGateway port
│   └── service.py     # Target-calendar I/O around the planner, with progress
├── ical_source.py     # iCal feed source (standard library parser)
├── google_client.py   # Google Calendar adapter (batch requests + retries)
├── auth.py            # Google OAuth, Desktop flow, token.json
├── config.py          # Settings from environment variables
├── cli/
│   ├── main.py        # Argument parsing and wiring of the adapters
│   ├── prompts.py     # Interactive preference editor (-i)
│   ├── console.py     # Colored console reporter
│   └── ansi.py        # ANSI styling helpers
└── tui/               # Optional: pip install ".[tui]"
    ├── model.py       # View model of the editor (pure Python, no Textual)
    ├── widgets.py     # Color picker, plan tree, swatches
    └── app.py         # Textual app: tabs, workers, reporter
```

## License

[MIT](LICENSE)
