# Polimi Calendar Coloring Sync

This script connects to your Google Calendar and synchronizes events from a read-only Polimi iCal subscription into a new, fully customizable Google Calendar. During the synchronization, it automatically color-codes your exams based on your enrollment status!

### Features

- **Intelligent Synchronization**: Safely copies events from a source calendar to a target calendar, updating only what has changed to minimize API calls and avoid rate limits.
- **Auto-Coloring Exams**: Automatically colors exams **Red** if you are subscribed ("Iscritto") and **Grey** if you are not ("Non iscritto").
- **Auto-Coloring Lectures**: Automatically assigns unique, consistent colors to different courses ("Lezione: Didattica - [Course Name]").
- **Interactive Customization**: Run the script with `-i` to manually choose your exam subscriptions or assign specific colors to your courses. When customizing, this ignores saved data and lets you start fresh!
- **Persistent Memory**: Saves your choices to `course_colors.json` and `exam_states.json` so your calendar stays perfectly coordinated across future automated syncs.
- **Environment Configuration**: Easily configure calendar names and credentials paths using a `.env` file.
- **Beautiful Terminal Output**: Features a concise, color-coded ANSI terminal interface so you know exactly what is happening.

---

## Prerequisites

1. **Python 3**: Make sure Python 3 is installed on your system.
2. **Google Cloud Account**: You need a Google Cloud account to generate API credentials.

## Setup Instructions

### 1. Configure Google Cloud Credentials

Because this script accesses your personal Google Calendar, you must create your own API credentials:

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

### 2. Configure Environment Variables

Copy the provided `.env.example` file to create your own `.env` file:

```bash
cp .env.example .env
```

Inside `.env`, you can customize the names of your source and target calendars if they differ from the defaults.

### 3. Install Dependencies

Open a terminal in the project directory and run:

```bash
pip install -r requirements.txt
```

---

## Usage

You can sync your entire calendar at once (**exams and lectures**), or specify an individual target.

### Basic Commands (Auto-Sync)

To silently sync your calendar using automated rules and previously saved preferences:

```bash
# Sync both exams and lectures (default)
python3 src/main.py

# Or explicitly specify a target
python3 src/main.py all
python3 src/main.py exams
python3 src/main.py lectures
```

_(Running the sync will read from `course_colors.json` and `exam_states.json` to preserve your past color choices)._

### Interactive Commands (Customization)

To customize or review your preferences, add the `-i` flag:

```bash
python3 src/main.py -i
python3 src/main.py exams -i
python3 src/main.py lectures -i
```

- **When running `exams -i`**: The script asks you questions per exam. It offers your current saved subscription status and color as defaults (press Enter to keep). You can pick custom colors or update subscriptions.
- **When running `lectures -i`**: The script iterates through your courses, showing your existing color as default (or a deterministic suggestion for new courses). Press Enter to keep, or enter a number 1-11 to customize.

### Verbose Logging

To see a detailed breakdown of decisions and batch operations, use the `-v` flag:

```bash
python3 src/main.py -v
```

---

## Important Note

Because the script creates a _copy_ of your exams to color them, **you should hide the original calendar** in your Google Calendar web interface or mobile app. Otherwise, you will see duplicates of every event! You can do this by unchecking the box next to the original "Polimi <student_id>" calendar in the sidebar.

## Running in the Cloud (GitHub Actions)

This repository includes a GitHub Actions workflow (`.github/workflows/manual_sync.yml`) that allows you to trigger the sync manually from the cloud without running it on your laptop!

### Setup Instructions for GitHub Actions

Because your API credentials are kept highly secure and ignored by Git, you must provide them to GitHub as Repository Secrets:

1. **Get your Base64 Token**: Since `token.pickle` is a binary file, you must convert it to text. Run this in your local terminal:
   ```bash
   base64 token.pickle -w 0
   ```
   _(Copy the giant string it outputs)_
2. **Add Secrets**: Go to your GitHub repository **Settings** -> **Secrets and variables** -> **Actions**. Click **New repository secret** and add:
   - `GCP_CREDENTIALS_JSON`: Paste the raw text contents of your `credentials.json` file.
   - `GCP_TOKEN_PICKLE_B64`: Paste the base64 string you copied in Step 1.
   - `SOURCE_CALENDAR_NAME`: custom source calendar name
   - `TARGET_CALENDAR_NAME`: custom target calendar name

### Using your custom colors in the cloud

By default, your custom color choices are in `course_colors.json` and `exam_states.json`.

---

## Architecture

The code follows SOLID principles and is modularized for clarity:

- `src/auth.py`: Handles Google OAuth 2.0.
- `src/calendar_client.py`: Wrapper for Google API interactions.
- `src/colors.py`: Centralized ANSI and Google Calendar color definitions.
- `src/sync_processor.py`: Coordinates the sync process and delta comparisons.
- `src/strategies/`: Contains the logic for event coloring (Open/Closed principle).
  - `base.py`: Abstract base classes and JSON persistence logic.
  - `exams.py`: Rules for coloring and auto-declining exams.
  - `lectures.py`: Rules for deterministic course coloring.
- `src/main.py`: Configures the environment and manages the command line interface.
