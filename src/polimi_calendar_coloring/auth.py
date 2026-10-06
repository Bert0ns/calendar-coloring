"""Google OAuth 2.0 (Desktop app flow) with a JSON token cache."""

from __future__ import annotations

import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

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
