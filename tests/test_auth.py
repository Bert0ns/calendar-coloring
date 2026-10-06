import pickle
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from calendar_coloring import auth
from calendar_coloring.auth import (
    Authenticator,
    CredentialsFileNotFoundError,
    LoginRequiredError,
)


class FakeCreds:
    """Picklable stand-in for google.oauth2.credentials.Credentials."""

    def __init__(
        self, valid=True, expired=False, refresh_token="r", refresh_error=False
    ):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_error = refresh_error
        self.refreshed = False

    def refresh(self, request) -> None:
        if self.refresh_error:
            raise RefreshError("revoked")
        self.refreshed = True
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return '{"token": "fake"}'


@pytest.fixture
def paths(tmp_path: Path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}")
    return credentials, tmp_path / "token.json", tmp_path / "token.pickle"


def make_auth(paths, login_result=None, info=None, allow_login=True):
    credentials, token, legacy = paths
    login = MagicMock(return_value=login_result or FakeCreds())
    authenticator = Authenticator(
        credentials,
        token,
        legacy,
        login=login,
        on_info=(info if info is not None else []).append,
        allow_browser_login=allow_login,
    )
    return authenticator, login


@pytest.fixture
def cached(monkeypatch):
    """Patches loading token.json to return the given credentials."""
    holder: dict = {}

    def from_file(path, scopes):
        assert scopes == auth.SCOPES
        if "error" in holder:
            raise holder["error"]
        return holder["creds"]

    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", from_file)
    return holder


def test_valid_cached_token_is_used_without_login(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds()
    authenticator, login = make_auth(paths)

    assert authenticator.get_credentials() is cached["creds"]
    login.assert_not_called()
    assert paths[1].read_text() == "{}"  # not rewritten


def test_expired_token_is_refreshed_and_saved(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds(valid=False, expired=True)
    authenticator, login = make_auth(paths)

    creds = authenticator.get_credentials()

    assert creds.refreshed
    login.assert_not_called()
    assert paths[1].read_text() == '{"token": "fake"}'


def test_revoked_refresh_token_triggers_login(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds(valid=False, expired=True, refresh_error=True)
    fresh = FakeCreds()
    messages: list[str] = []
    authenticator, login = make_auth(paths, login_result=fresh, info=messages)

    assert authenticator.get_credentials() is fresh
    login.assert_called_once_with(paths[0])
    assert any("Logging in again" in m for m in messages)


def test_unexpected_refresh_errors_propagate(paths, cached) -> None:
    paths[1].write_text("{}")
    creds = FakeCreds(valid=False, expired=True)
    creds.refresh = MagicMock(side_effect=OSError("network down"))
    cached["creds"] = creds
    authenticator, _ = make_auth(paths)

    with pytest.raises(OSError):
        authenticator.get_credentials()


def test_invalid_token_without_refresh_token_triggers_login(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds(valid=False, expired=True, refresh_token=None)
    authenticator, login = make_auth(paths)
    authenticator.get_credentials()
    login.assert_called_once()


def test_unreadable_token_triggers_login(paths, cached) -> None:
    paths[1].write_text("garbage")
    cached["error"] = ValueError("bad")
    authenticator, login = make_auth(paths)
    authenticator.get_credentials()
    login.assert_called_once()
    assert paths[1].read_text() == '{"token": "fake"}'


def test_first_login_saves_json_token(paths) -> None:
    authenticator, login = make_auth(paths)
    authenticator.get_credentials()
    login.assert_called_once_with(paths[0])
    assert paths[1].read_text() == '{"token": "fake"}'


def test_missing_client_file_raises_only_when_login_is_needed(paths, cached) -> None:
    paths[0].unlink()
    authenticator, login = make_auth(paths)
    with pytest.raises(CredentialsFileNotFoundError) as exc_info:
        authenticator.get_credentials()
    assert exc_info.value.path == paths[0]
    assert isinstance(exc_info.value, FileNotFoundError)
    login.assert_not_called()

    paths[1].write_text("{}")
    cached["creds"] = FakeCreds()
    assert authenticator.get_credentials() is cached["creds"]


def test_legacy_pickle_is_migrated_to_json(paths) -> None:
    paths[2].write_bytes(pickle.dumps(FakeCreds()))
    messages: list[str] = []
    authenticator, login = make_auth(paths, info=messages)

    creds = authenticator.get_credentials()

    assert isinstance(creds, FakeCreds)
    login.assert_not_called()
    assert paths[1].read_text() == '{"token": "fake"}'
    assert any("Migrating" in m for m in messages)


def test_expired_legacy_pickle_is_refreshed_then_migrated(paths) -> None:
    paths[2].write_bytes(pickle.dumps(FakeCreds(valid=False, expired=True)))
    authenticator, login = make_auth(paths)

    assert authenticator.get_credentials().refreshed
    assert paths[1].exists()
    login.assert_not_called()


def test_json_token_takes_precedence_over_legacy_pickle(paths, cached) -> None:
    paths[1].write_text("{}")
    paths[2].write_bytes(b"not a pickle")
    cached["creds"] = FakeCreds()
    authenticator, _ = make_auth(paths)
    assert authenticator.get_credentials() is cached["creds"]


def test_without_legacy_path(paths) -> None:
    credentials, token, _ = paths
    authenticator = Authenticator(credentials, token, login=lambda _: FakeCreds())
    assert authenticator.get_credentials().valid


def test_non_interactive_session_fails_fast_instead_of_hanging(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds(valid=False, expired=True, refresh_error=True)
    authenticator, login = make_auth(paths, allow_login=False)

    with pytest.raises(LoginRequiredError):
        authenticator.get_credentials()
    login.assert_not_called()


def test_non_interactive_session_works_with_valid_token(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds()
    authenticator, _ = make_auth(paths, allow_login=False)
    assert authenticator.get_credentials() is cached["creds"]


def test_announces_browser_login(paths) -> None:
    messages: list[str] = []
    authenticator, _ = make_auth(paths, info=messages)
    authenticator.get_credentials()
    assert any("Opening the browser" in m for m in messages)


def test_login_flow_falls_back_to_manual_url_without_browser(monkeypatch) -> None:
    flow = MagicMock()
    flow.run_local_server.side_effect = [RuntimeError("no browser"), "creds"]
    from_file = MagicMock(return_value=flow)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", from_file)

    assert auth._run_installed_app_flow(Path("credentials.json")) == "creds"

    from_file.assert_called_once_with("credentials.json", auth.SCOPES)
    assert flow.run_local_server.call_args_list[1].kwargs == {
        "port": 0,
        "open_browser": False,
    }
