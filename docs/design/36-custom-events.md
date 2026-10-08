# Design Doc: Custom Events & Calendar Adoption (Issue #36)

**Issue:** [#36: feat: add custom events](https://github.com/Bert0ns/uni-calendar-coloring/issues/36)  
**Status:** In Progress / Implementing  
**Author:** Bert0ns & Antigravity  

---

## 1. Problem Statement & Motivation

Students often maintain academic and personal events outside the official university schedule (e.g. study groups, project sprint syncs, office hours, personal deadlines, extracurriculars). Currently:
1. **Unmanaged Events Are Preserved but Disconnected:** `uni-calendar-coloring` preserves events in the target calendar that lack the `calendar_coloring_managed` property, but provides no mechanism to create, track, or customize them through the application.
2. **No Portability for Custom Events:** Any custom event manually added in Google Calendar is absent from `profile.json`. If a student uses multiple machines or runs automated syncs via GitHub Actions, custom events cannot be managed declaratively or reproduced from the profile.
3. **No Way to Adopt Existing Events:** Students who already created custom events in their target calendar cannot bring them under `unical`'s management without recreating them manually.

---

## 2. Core Decisions & Specifications

Based on the requirements in Issue #36 and architectural discussion:

1. **Hybrid Architecture (Profile JSON + Target Calendar Adoption):**
   - **Declarative in Profile:** Custom events are defined and stored in `profile.json` under `custom_events`.
   - **Target Calendar Adoption:** `unical` provides a detection mechanism that scans the target calendar for unmanaged events and allows the user to adopt them into `profile.json`.
2. **Full Calendar Attribute Fidelity:**
   - A custom event supports all native calendar attributes:
     - `summary` (Title)
     - `description` (Body / Notes)
     - `location` (Physical location or virtual meeting URL)
     - `start` & `end` (Date or DateTime with time zone)
     - `color_id` (Google Calendar color palette identifier)
     - `recurrence` (Optional list of RFC 5545 `RRULE` strings, e.g. `["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"]`)
3. **Tagging and Managed Distinction:**
   - Target calendar events created/managed by `unical` for custom events carry:
     - `calendar_coloring_managed = "true"`
     - `calendar_coloring_custom = "true"`
   - This ensures custom events are:
     - Identified as managed by `unical` (not treated as third-party unmanaged events).
     - Distinct from university mirror events (immune to university rule re-evaluation, course filtering, or lecture pruning).
4. **Adoption & Detection Workflow:**
   - Unical queries the target calendar for events lacking `calendar_coloring_managed`.
   - The user inspects detected events and chooses which to adopt.
   - Selected events are added to `profile.json`'s `custom_events` and tagged with `calendar_coloring_custom = "true"` in the target calendar.
5. **Sync Integration:**
   - The `SyncPlanner` is updated to plan mutations for custom events in parallel with university source events.
   - Profile-defined custom events are inserted into the target calendar if missing, updated if modified in `profile.json`, and deleted from the target calendar if removed from `profile.json`.
6. **CLI & TUI Interfaces:**
   - CLI subcommands: `unical events list`, `unical events add`, `unical events delete`, and `unical events adopt`.
   - TUI: A dedicated "Custom Events" tab offering visual event browsing, creation modal, and adoption dialog.

---

## 3. Data Model

### 3.1 `CustomEvent` Representation

```python
@dataclass(frozen=True)
class CustomEvent:
    id: str
    summary: str
    start: dict[str, Any]
    end: dict[str, Any]
    description: str = ""
    location: str = ""
    color_id: str | None = None
    recurrence: tuple[str, ...] = ()
    source: str = "created"  # "created" | "adopted"
```

### 3.2 Serialization in `profile.json`

```json
{
    "version": 1,
    "name": "Politecnico di Milano",
    "calendars": {
        "source": "Calendar",
        "target": "Calendar Colored",
        "time_zone": "Europe/Rome"
    },
    "rules": [ ... ],
    "enrollment": { ... },
    "courses": { ... },
    "exams": { ... },
    "deadlines": { ... },
    "custom_events": [
        {
            "id": "cst_study_group_01",
            "summary": "Distributed Systems Study Group",
            "description": "Weekly review of lecture problem sets",
            "location": "Building 14, Room 2.1",
            "start": {"dateTime": "2026-10-12T14:00:00+02:00"},
            "end": {"dateTime": "2026-10-12T16:00:00+02:00"},
            "color_id": "7",
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261220T235959Z"],
            "source": "created"
        }
    ]
}
```

---

## 4. Lifecycle & Sync Architecture

```
                    ┌───────────────────────────┐
                    │       profile.json        │
                    │  (custom_events: [...])   │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
┌──────────────────────┐   ┌─────────────┐   ┌──────────────────────┐
│  University Source   │───▶│ SyncPlanner │◀───│   Target Calendar    │
│  (iCal / Source Cal) │   └──────┬──────┘   │ (Google Calendar)    │
└──────────────────────┘          │          └──────────┬───────────┘
                                  ▼                     │
                           ┌─────────────┐              │
                           │  SyncPlan   │              │
                           │ (mutations) │              │
                           └──────┬──────┘              │
                                  ▼                     │
                           ┌─────────────┐              │
                           │ SyncService │              │
                           └─────────────┘              │
                                                        │
                      Detection & Adoption Flow         │
                                                        │
   ┌────────────────────────────────────────────────────┘
   │ Unmanaged events (no managed tag)
   ▼
┌──────────────────────┐
│ Event Detection &    │───▶ User Selects Events ───▶ Added to profile.json
│ Adoption Manager     │                            Tagged in Google Calendar
└──────────────────────┘
```

### 4.1 Sync Planner Rules for Custom Events
1. **Insert:** Custom event present in `profile.custom_events` but missing from target calendar $\rightarrow$ `INSERT` mutation with `calendar_coloring_managed = "true"` and `calendar_coloring_custom = "true"`.
2. **Update:** Custom event present in both profile and target calendar, but differences detected in `summary`, `description`, `location`, `color_id`, `start`, `end`, or `recurrence` $\rightarrow$ `UPDATE` mutation.
3. **Delete:** Event in target calendar tagged with `calendar_coloring_custom = "true"` but absent from `profile.custom_events` $\rightarrow$ `DELETE` mutation (unless outside active sync window).
4. **Isolation:** University rule changes, course filters (`--course`), and pruning (`--prune-before`) do not delete or modify custom events.
5. **Preservation:** Events in target calendar lacking `calendar_coloring_managed` remain `preserved_unmanaged`.

---

## 5. User Interface (CLI & TUI)

### 5.1 CLI Commands
- `unical events list`: Lists all custom events saved in profile.
- `unical events add`: Interactive wizard or CLI flags (`--summary`, `--start`, `--end`, `--color`, `--recurring`) to create a new custom event.
- `unical events delete <event-id>`: Removes custom event from profile.
- `unical events adopt`: Detects unmanaged events in the target calendar and interactively prompts the user to select which events to adopt into `profile.json`.

### 5.2 TUI (Textual) *(Planned for Follow-Up Phase)*
> [!NOTE]
> Initial delivery in PR #39 implements the core data model, sync planner integration, and CLI commands (`unical events list|add|delete|adopt`). The interactive Textual TUI tab is scoped for a follow-up phase.

- **New Tab:** "Custom Events" tab in `src/unical/tui/app.py`.
- **Event List & Preview:** Shows scheduled custom events, times, recurrence tags, and color badges.
- **Actions:**
  - `[+ New Custom Event]`: Opens form to enter details.
  - `[Adopt From Calendar]`: Queries target calendar for unmanaged events and presents a selectable list to import.
  - `[Delete Event]`: Removes selected custom event.
