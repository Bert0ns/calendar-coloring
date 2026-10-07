"""Google OAuth 2.0 (Desktop app flow) with a JSON token cache."""

from __future__ import annotations

import json
import pickle
import shutil
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from unical.config import default_credentials_path

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CredentialsFileNotFoundError(FileNotFoundError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"OAuth client file '{path}' not found.")
        self.path = path


class LoginRequiredError(RuntimeError):
    """A browser login is needed but the session is not interactive."""

    def __init__(self) -> None:
        super().__init__(
            "Google login required, but no interactive terminal is available. "
            "Run the tool locally once to log in, then update the GCP_TOKEN_JSON "
            "secret if you use GitHub Actions."
        )


class InvalidCredentialsError(ValueError):
    """The provided credentials file does not contain a valid OAuth client configuration."""


def clean_path_input(raw: str) -> Path:
    """Clean a raw file path string from terminal input or drag-and-drop.

    Handles wrapping quotes, backslash-escaped spaces, file:// URIs, and user home expansion (~).
    """
    path_str = raw.strip()
    if path_str.startswith("file://"):
        parsed = urllib.parse.urlparse(path_str)
        path_str = urllib.parse.unquote(parsed.path)
        if len(path_str) > 2 and path_str[0] == "/" and path_str[2] == ":":
            path_str = path_str[1:]
    if (path_str.startswith('"') and path_str.endswith('"')) or (
        path_str.startswith("'") and path_str.endswith("'")
    ):
        path_str = path_str[1:-1].strip()
    if r"\ " in path_str:
        path_str = path_str.replace(r"\ ", " ")
    return Path(path_str).expanduser()


def validate_client_secrets(
    content_or_data: str | bytes | dict[str, Any],
) -> dict[str, Any]:
    """Validate that the given content represents a valid Google OAuth client JSON file.

    Returns the parsed JSON data dict on success.
    Raises InvalidCredentialsError if invalid.
    """
    if isinstance(content_or_data, (str, bytes)):
        try:
            data = json.loads(content_or_data)
        except json.JSONDecodeError as exc:
            raise InvalidCredentialsError(f"The file is not valid JSON: {exc}") from exc
    elif isinstance(content_or_data, dict):
        data = content_or_data
    else:
        raise InvalidCredentialsError("Expected JSON text or dictionary.")

    if not isinstance(data, dict):
        raise InvalidCredentialsError(
            "The JSON file must contain an object at the top level."
        )

    if data.get("type") == "service_account":
        raise InvalidCredentialsError(
            "This file is a Google Cloud Service Account key, not an OAuth 2.0 Client ID. "
            "Uni Calendar Coloring requires an OAuth Client ID for a Desktop application. "
            "Please create an OAuth Client ID under 'APIs & Services → Credentials' in Google Cloud Console."
        )

    client_info = data.get("installed") or data.get("web")
    if not client_info:
        raise InvalidCredentialsError(
            "The file does not look like a Google OAuth 2.0 client secret file. "
            "Expected top-level key 'installed' (Desktop app) or 'web'."
        )

    if not isinstance(client_info, dict):
        raise InvalidCredentialsError(
            "Invalid OAuth client format: expected an object inside 'installed'."
        )

    if not client_info.get("client_id"):
        raise InvalidCredentialsError("The OAuth client file is missing 'client_id'.")

    if not client_info.get("client_secret"):
        raise InvalidCredentialsError(
            "The OAuth client file is missing 'client_secret'."
        )

    return data


def import_credentials(
    source: Path | str, destination: Path | None = None, move: bool = False
) -> Path:
    """Validate and copy or move a credentials file to the destination."""
    source_path = clean_path_input(str(source)).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Credentials file '{source_path}' does not exist.")
    if not source_path.is_file():
        raise InvalidCredentialsError(f"'{source_path}' is not a regular file.")

    content = source_path.read_text(encoding="utf-8")
    validate_client_secrets(content)

    dest_path = (destination or default_credentials_path()).resolve()

    if source_path == dest_path:
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source_path), str(dest_path))
    else:
        shutil.copy2(str(source_path), str(dest_path))
    return dest_path


def _run_installed_app_flow(credentials_path: Path) -> Any:
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    try:
        return flow.run_local_server(port=0)
    except Exception:
        # No usable browser (e.g. WSL/SSH): print the URL to open manually.
        return flow.run_local_server(port=0, open_browser=False)


class Authenticator:
    """Returns valid user credentials, logging in through the browser if needed.

    The token is cached as JSON in ``token_path``. A token cached by older
    versions as ``token.pickle`` (``legacy_token_path``) is migrated once.
    """

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        legacy_token_path: Path | None = None,
        login: Callable[[Path], Any] = _run_installed_app_flow,
        on_info: Callable[[str], None] = lambda _: None,
        allow_browser_login: bool = True,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.legacy_token_path = (
            Path(legacy_token_path) if legacy_token_path is not None else None
        )
        self._login = login
        self._on_info = on_info
        self.allow_browser_login = allow_browser_login

    def get_credentials(self) -> Any:
        creds, from_legacy = self._load_cached()
        if creds is not None and creds.valid:
            if from_legacy:
                self._save(creds)
            return creds

        if creds is not None and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                self._on_info("Saved login expired or was revoked. Logging in again.")
                creds = None

        if creds is None or not creds.valid:
            if not self.credentials_path.exists():
                raise CredentialsFileNotFoundError(self.credentials_path)
            if not self.allow_browser_login:
                raise LoginRequiredError()
            self._on_info("Opening the browser to log in with Google...")
            creds = self._login(self.credentials_path)

        self._save(creds)
        return creds

    def _load_cached(self) -> tuple[Any, bool]:
        if self.token_path.exists():
            try:
                return (
                    Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                        str(self.token_path), SCOPES
                    ),
                    False,
                )
            except (ValueError, KeyError):
                self._on_info(f"Ignoring unreadable token file '{self.token_path}'.")
                return None, False
        if self.legacy_token_path is not None and self.legacy_token_path.exists():
            # Only ever reads the user's own token written by a previous version.
            with self.legacy_token_path.open("rb") as f:
                creds = pickle.load(f)
            self._on_info(
                f"Migrating '{self.legacy_token_path}' to '{self.token_path}'."
            )
            return creds, True
        return None, False

    def _save(self, creds: Any) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
