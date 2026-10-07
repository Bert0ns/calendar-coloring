import json
import pickle
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from unical import auth
from unical.auth import (
    Authenticator,
    CredentialsFileNotFoundError,
    InvalidCredentialsError,
    LoginRequiredError,
    clean_path_input,
    import_credentials,
    validate_client_secrets,
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

    creds = authenticator.get_credentials()

    assert creds is fresh
    login.assert_called_once_with(paths[0])
    assert paths[1].read_text() == '{"token": "fake"}'
    assert any("expired or was revoked" in m for m in messages)


def test_expired_token_without_refresh_token_triggers_login(paths, cached) -> None:
    paths[1].write_text("{}")
    cached["creds"] = FakeCreds(valid=False, expired=True, refresh_token=None)
    fresh = FakeCreds()
    authenticator, login = make_auth(paths, login_result=fresh)

    assert authenticator.get_credentials() is fresh
    login.assert_called_once_with(paths[0])


def test_login_when_no_token_file(paths) -> None:
    fresh = FakeCreds()
    authenticator, login = make_auth(paths, login_result=fresh)

    assert authenticator.get_credentials() is fresh
    login.assert_called_once_with(paths[0])
    assert paths[1].read_text() == '{"token": "fake"}'


def test_corrupted_token_file_triggers_login(paths, cached) -> None:
    paths[1].write_text("not json")
    cached["error"] = ValueError("bad json")
    fresh = FakeCreds()
    messages: list[str] = []
    authenticator, login = make_auth(paths, login_result=fresh, info=messages)

    assert authenticator.get_credentials() is fresh
    login.assert_called_once_with(paths[0])
    assert any("Ignoring unreadable" in m for m in messages)


def test_missing_credentials_file_raises_custom_error(tmp_path: Path) -> None:
    authenticator, login = make_auth(
        (tmp_path / "nope.json", tmp_path / "t.json", None)
    )

    with pytest.raises(CredentialsFileNotFoundError) as exc_info:
        authenticator.get_credentials()

    assert exc_info.value.path == tmp_path / "nope.json"
    assert "nope.json" in str(exc_info.value)
    assert isinstance(exc_info.value, FileNotFoundError)
    login.assert_not_called()


def test_valid_token_bypasses_credentials_file_check(paths, cached) -> None:
    paths[0].unlink()
    authenticator, login = make_auth(paths)

    with pytest.raises(CredentialsFileNotFoundError) as exc_info:
        authenticator.get_credentials()
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


# -- Credential validation, cleaning, and import --------------------------------


def test_clean_path_input() -> None:
    assert clean_path_input("  /path/to/file.json  ") == Path("/path/to/file.json")
    assert clean_path_input("'/path/to/file.json'") == Path("/path/to/file.json")
    assert clean_path_input('"/path/to/file.json"') == Path("/path/to/file.json")
    assert clean_path_input(r"/path/to/my\ file.json") == Path("/path/to/my file.json")
    assert clean_path_input("file:///tmp/creds.json") == Path("/tmp/creds.json")
    assert clean_path_input("~/creds.json") == Path.home() / "creds.json"


def test_validate_client_secrets_valid_installed() -> None:
    payload = {
        "installed": {
            "client_id": "test-client-id.apps.googleusercontent.com",
            "client_secret": "secret123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    result = validate_client_secrets(json.dumps(payload))
    assert result == payload
    assert validate_client_secrets(payload) == payload


def test_validate_client_secrets_valid_web() -> None:
    payload = {
        "web": {
            "client_id": "test-web-id.apps.googleusercontent.com",
            "client_secret": "secret456",
        }
    }
    assert validate_client_secrets(payload) == payload


def test_validate_client_secrets_invalid_json() -> None:
    with pytest.raises(InvalidCredentialsError, match="not valid JSON"):
        validate_client_secrets("{not valid json")


def test_validate_client_secrets_non_dict() -> None:
    with pytest.raises(InvalidCredentialsError, match="contain an object"):
        validate_client_secrets("[1, 2, 3]")
    with pytest.raises(InvalidCredentialsError, match="Expected JSON text"):
        validate_client_secrets(12345)  # type: ignore[arg-type]


def test_validate_client_secrets_service_account() -> None:
    payload = {
        "type": "service_account",
        "project_id": "my-project",
        "private_key_id": "key123",
    }
    with pytest.raises(InvalidCredentialsError, match="Service Account key"):
        validate_client_secrets(payload)


def test_validate_client_secrets_missing_installed_or_web() -> None:
    payload = {"some_other_key": {}}
    with pytest.raises(
        InvalidCredentialsError, match="Expected top-level key 'installed'"
    ):
        validate_client_secrets(payload)


def test_validate_client_secrets_missing_keys() -> None:
    with pytest.raises(InvalidCredentialsError, match="missing 'client_id'"):
        validate_client_secrets({"installed": {"client_secret": "s"}})

    with pytest.raises(InvalidCredentialsError, match="missing 'client_secret'"):
        validate_client_secrets({"installed": {"client_id": "c"}})


def test_import_credentials_copies_file(tmp_path: Path) -> None:
    source = tmp_path / "downloaded_creds.json"
    source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                }
            }
        )
    )
    dest = tmp_path / "config" / "unical" / "credentials.json"

    result = import_credentials(source, destination=dest)
    assert result == dest.resolve()
    assert dest.exists()
    assert source.exists()  # original kept on copy
    assert json.loads(dest.read_text())["installed"]["client_id"] == "client-id"


def test_import_credentials_moves_file(tmp_path: Path) -> None:
    source = tmp_path / "move_me.json"
    source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                }
            }
        )
    )
    dest = tmp_path / "moved" / "credentials.json"

    result = import_credentials(source, destination=dest, move=True)
    assert result == dest.resolve()
    assert dest.exists()
    assert not source.exists()  # moved away


def test_import_credentials_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.json"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        import_credentials(missing)

    directory = tmp_path / "somedir"
    directory.mkdir()
    with pytest.raises(InvalidCredentialsError, match="not a regular file"):
        import_credentials(directory)

    invalid_file = tmp_path / "bad.json"
    invalid_file.write_text("not json")
    with pytest.raises(InvalidCredentialsError, match="not valid JSON"):
        import_credentials(invalid_file)


def test_import_credentials_same_path(tmp_path: Path) -> None:
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "id",
                    "client_secret": "secret",
                }
            }
        )
    )
    assert import_credentials(creds, destination=creds) == creds.resolve()
