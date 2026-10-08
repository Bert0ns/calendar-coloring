# Design Doc: Long Calendar Management (Issue #37)

**Issue:** [#37: need to better understand how long calendars will become to manage](https://github.com/Bert0ns/uni-calendar-coloring/issues/37)  
**Status:** Approved / In Design  
**Author:** Bert0ns & Antigravity  

---

## 1. Problem Statement & Motivation

As students progress through multiple semesters and academic years, the volume of events managed by `uni-calendar-coloring` grows significantly:
1. **Network & Quota Waste:** Currently, `GoogleCalendarClient.get_all_events()` fetches the user's entire calendar history without `timeMin` or `timeMax`. Over 2–3 years, this downloads thousands of historical events on every single sync run.
2. **Accidental Historical Modifications:** A change to course colors, title formats, or classification rules re-evaluates all historical events. A student who tweaks their profile could unintentionally trigger hundreds of API mutations across past semesters.
3. **Google API Quota Pressure:** University calendars (e.g. PoliMi WeBeep) typically issue discrete events for every lecture rather than recurring `RRULE`s (yielding 200–300+ events per semester). Mass-updating multiple years in a single run risks triggering Google Calendar rate limits (`403 rateLimitExceeded` / `429 Too Many Requests`).
4. **Preservation of History:** Students want past semesters to remain visible in their personal calendars as an archive, but **frozen** against subsequent automatic updates or deletions.

---

## 2. Core Decisions & Specifications

Based on design discussion for Issue #37:

1. **Past and Distant Future Events Frozen by Default:**
   - Events falling outside the active window are **ignored**:
     - In the **target calendar**: they are left untouched (never modified, never deleted).
     - In the **source calendar**: they are not ingested/inserted into the target.
2. **Academic Semester Auto-Inference:**
   - Academic calendars are divided into two primary semesters:
     - **Semester 1 (Fall / Winter):** September 16 $\rightarrow$ February 28 (or 29 in leap years). Covers fall lectures and winter exam sessions.
     - **Semester 2 (Spring / Summer):** March 1 $\rightarrow$ September 15. Covers spring lectures, summer exams, and September retake exams.
   - The current semester window is automatically inferred from the system date (`today`).
3. **Manual Window Override:**
   - Users can override the inferred boundaries via CLI flags (`--from`, `--to`) or through the TUI.
   - An `--all-time` flag allows intentional full-history rebuilds when needed.
4. **Server-Side Fetching Optimization (Option B):**
   - Use `timeMin` and `timeMax` parameters in Google Calendar API calls (`events().list`) so that old and distant future events are never retrieved over the wire.
   - Filter `VEVENT` parsing in `ical_source.py` to discard events outside the active window at ingestion.
   - When `--prune-before` is specified with an older date than `window_from`, `timeMin` adjusts to `min(window_from, prune_before)` so prune deletions can still execute.
5. **Selective Sync by Course:**
   - Extend selective sync capabilities beyond kind (`lectures`, `exams`, `deadlines`) to allow filtering by a specific course (e.g. `--course "Informatica Teorica"`).
6. **TUI Integration:**
   - **Setup Tab:** Window selector (Auto Semester / Custom Dates / All Time) and Course filter dropdown.
   - **Sync Tab:** Sticky reminder banner displaying the active sync window and course scope before applying mutations.

---

## 3. Semester Window Model

### 3.1 Date Inference Rules

```text
               Academic Year Calendar Partitioning
 01/01                     03/01                  09/15 09/16                    12/31
┌─────────────────────────┬────────────────────────────┬──────────────────────────────┐
│  Semester 1 (concl.)    │        Semester 2          │        Semester 1            │
│  (starts prev Sep 16)   │  03/01 -> 09/15 (current)  │  09/16 -> 02/28/29 (next yr) │
└─────────────────────────┴────────────────────────────┴──────────────────────────────┘
```

The algorithm to resolve the default window for a given date $D$ (`today`):
- **If $D \in [\text{March 1}, \text{September 15}]$:**
  - Active Semester: **Semester 2**
  - `start_date` = `date(D.year, 3, 1)`
  - `end_date` = `date(D.year, 9, 15)`
- **If $D \in [\text{September 16}, \text{December 31}]$:**
  - Active Semester: **Semester 1**
  - `start_date` = `date(D.year, 9, 16)`
  - `end_date` = `date(D.year + 1, 2, 29 if is_leap(D.year + 1) else 28)`
- **If $D \in [\text{January 1}, \text{February 28/29}]$:**
  - Active Semester: **Semester 1**
  - `start_date` = `date(D.year - 1, 9, 16)`
  - `end_date` = `date(D.year, 2, 29 if is_leap(D.year) else 28)`

### 3.2 Event Date Evaluation
An event is within window $[W_{\text{start}}, W_{\text{end}}]$ if:
$$W_{\text{start}} \le \text{start\_day}(E) \le W_{\text{end}}$$
- Events with $\text{start\_day}(E) < W_{\text{start}}$: **Frozen past** (untouched in target, skipped in source).
- Events with $\text{start\_day}(E) > W_{\text{end}}$: **Frozen future** (untouched in target, skipped in source).

---

## 4. Architectural & Component Changes

```
┌────────────────────────────────────────────────────────────────────────┐
│                               CLI / TUI                                │
│   --from, --to, --all-time, --course / Setup tab & Sync tab reminder  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                              Workflow                                  │
│   SyncOptions(window_from, window_to, course, target, ...)            │
└──────────────────┬────────────────────────────────┬────────────────────┘
                   │                                │
                   ▼                                ▼
┌─────────────────────────────────────┐  ┌───────────────────────────────┐
│           Event Sources             │  │        CalendarGateway        │
│  - ical_source: ingests within win  │  │  - get_all_events(            │
│  - google_client: timeMin / timeMax │  │      time_min, time_max)      │
└──────────────────┬──────────────────┘  └──────────────┬────────────────┘
                   │                                    │
                   └─────────────────┬──────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                             SyncPlanner                                │
│   - Only diffs events within window and matching course filter         │
│   - Preserves events outside window without generating mutations       │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Data Models & Protocols

1. **`SyncOptions` (`unical/workflow.py`)**:
   ```python
   @dataclass(frozen=True)
   class SyncOptions:
       target: SyncTarget = SyncTarget.ALL
       interactive: bool = False
       dry_run: bool = False
       prune_before: date | None = None
       window_from: date | None = None      # None when --all-time is active
       window_to: date | None = None        # None when --all-time is active
       course: str | None = None            # Substring / exact course name filter
   ```

2. **`CalendarGateway` Protocol (`unical/sync/gateway.py`)**:
   ```python
   def get_all_events(
       self,
       calendar_id: str,
       expand_recurring: bool = True,
       time_min: datetime | None = None,
       time_max: datetime | None = None,
   ) -> list[Event]: ...
   ```

3. **`GoogleCalendarClient` (`unical/google_client.py`)**:
   - `events().list()` receives `timeMin` and `timeMax` as ISO-8601 strings (formatted with the target calendar's time zone offset or UTC `Z`).
   - If `prune_before` is provided and is earlier than `window_from`, `time_min` is set to `prune_before` so that prune targets can be queried and deleted.

4. **`EventSource` / `ical_source.py`**:
   - `fetch_events` receives or uses the sync window to filter out events before parsing/converting them.

### 4.2 Planning & Filtering (`unical/sync/planner.py`)

1. **Scope Checking**:
   The planner's `in_scope` predicate checks both kind and course filter:
   ```python
   def in_scope(event: Event) -> bool:
       if not options.target.covers(classifier.kind_of(event)):
           return False
       if options.course:
           classification = classifier.classify(event)
           if not classification or options.course.lower() not in classification.name.lower():
               return False
       return True
   ```
2. **Window Enforcement**:
   - In `plan()`, any event whose `start_day` is $< W_{\text{start}}$ or $> W_{\text{end}}$ is marked out-of-scope and skipped from mutation calculations.
   - They are **not** considered "missing from source", so they will never trigger a `DELETE` mutation on the target calendar.

---

## 5. CLI & TUI Interface Design

### 5.1 CLI Commands & Flags

```bash
# Default: Automatically syncs the current semester window
unical

# Sync only a specific course within the current semester
unical --course "Software Engineering"

# Custom time boundaries
unical --from 2026-03-01 --to 2026-06-30

# Sync an entire academic year or multi-year history (disables freezing)
unical --all-time

# Combined with existing kind targets and flags
unical lectures --course "Algorithms" --from 2026-03-01 --dry-run -v
```

### 5.2 TUI Interface

1. **Setup Tab**:
   - **Sync Window Section**:
     - Radio group / Select:
       - `(•) Current Semester (Auto)` [e.g. `2026-03-01` to `2026-09-15`]
       - `( ) Custom Window`
       - `( ) All Time`
     - Two input boxes (`From:` and `To:`), active when `Custom Window` is selected.
   - **Course Filter Section**:
     - Dropdown listing `All Courses` plus every course detected in the active window.
2. **Sync Tab**:
   - A persistent status reminder bar positioned directly above the preview list and Apply button:
     ```text
     ┌────────────────────────────────────────────────────────────────────────┐
     │ 📅 Window: 2026-03-01 -> 2026-09-15 (Semester 2)  |  📚 Course: All   │
     └────────────────────────────────────────────────────────────────────────┘
     ```

---

## 6. Edge Cases & Considerations

1. **Multi-Day Events Spanning Boundaries:**
   - Evaluated by `start_day`. If an event starts on or before `window_to`, it is considered inside the window.
2. **Midnight / Time Zone Offsets:**
   - `timeMin` is converted to `00:00:00` in the calendar's time zone; `timeMax` is converted to `23:59:59.999999` of `window_to` so that boundary dates are fully inclusive.
3. **Leap Years:**
   - Tested explicitly for February 28 vs 29.
4. **Interaction with `--prune-before`:**
   - `--prune-before` is an explicit instruction to clean up past events.
   - If `--prune-before 2026-01-01` is passed, events before that date are marked for deletion even if they precede `window_from`.
   - The query window for target events expands its `timeMin` to `min(window_from, prune_before)` so it can find and delete them.
5. **No Events Found in Window:**
   - If a source has no events in the active semester, `unical` displays a clear notice:
     `"No events found in current semester window (2026-03-01 -> 2026-09-15). Use --all-time or --from/--to to adjust."`

---

## 7. Implementation Plan

1. **Phase 1: Date & Semester Utilities**
   - Add `unical.semesters` module with `current_semester_window(today: date | None = None)` and leap-year-aware calculations.
   - Unit tests covering all edge dates (leap day, boundary transitions on Sep 15/16 and Feb 28/Mar 1).
2. **Phase 2: Source & Gateway Optimization**
   - Update `CalendarGateway.get_all_events` to accept `time_min` and `time_max`.
   - Update `GoogleCalendarClient` to query with `timeMin` and `timeMax`.
   - Update `ical_source.py` to filter events during stream parsing.
3. **Phase 3: Planner & Workflow Windowing + Course Filter**
   - Add `window_from`, `window_to`, and `course` to `SyncOptions`.
   - Update `SyncPlanner` to enforce freeze boundaries and course matching.
4. **Phase 4: CLI Integration**
   - Expose `--from`, `--to`, `--all-time`, and `--course` in `unical/cli/main.py`.
5. **Phase 5: TUI Integration**
   - Add window and course controls to the Setup tab in `unical/tui/setup.py`.
   - Add reminder widget to the Sync tab in `unical/tui/widgets.py` / `unical/tui/app.py`.
6. **Phase 6: Verification & Regression Testing**
   - Golden fixture testing, unit tests, and integration test passes.
