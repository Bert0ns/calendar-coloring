import os
import pickle
from typing import ClassVar

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow


class Authenticator:
    """Handles Google OAuth 2.0 authentication."""

    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, credentials_path="credentials.json", token_path="token.pickle"):
        self.credentials_path = credentials_path
        self.token_path = token_path

    def get_credentials(self):
        creds = None
        if os.path.exists(self.token_path):
            with open(self.token_path, "rb") as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    # If the refresh token is revoked or invalid, delete the file and force re-auth
                    if os.path.exists(self.token_path):
                        os.remove(self.token_path)
                    creds = None

            if not creds or not creds.valid:
                if os.getenv("CI") == "true":
                    raise RuntimeError(
                        "Google credentials could not be loaded or refreshed in CI. "
                        "Please verify your GCP_CREDENTIALS_JSON and GCP_TOKEN_PICKLE_B64 repository secrets."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES
                )
                try:
                    creds = flow.run_local_server(port=0)
                except Exception:
                    creds = flow.run_local_server(port=0, open_browser=False)

            with open(self.token_path, "wb") as token:
                pickle.dump(creds, token)

        return creds
