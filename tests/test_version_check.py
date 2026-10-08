from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

from unical.version_check import (
    AsyncUpdateChecker,
    VersionCheckResult,
    check_for_updates,
    fetch_latest_version,
    format_upgrade_notice,
    is_newer_version,
    read_version_cache,
    should_check_for_updates,
    write_version_cache,
)


def test_is_newer_version_comparisons() -> None:
    assert is_newer_version("1.0.2", "1.0.1") is True
    assert is_newer_version("1.1.0", "1.0.1") is True
    assert is_newer_version("2.0.0", "1.0.1") is True
    assert is_newer_version("v1.0.2", "v1.0.1") is True
    assert is_newer_version("1.0.1", "1.0.1") is False
    assert is_newer_version("1.0.0", "1.0.1") is False
    assert is_newer_version("0.9.9", "1.0.1") is False
    assert is_newer_version("invalid", "1.0.1") is False
    assert is_newer_version("1.0.1", "invalid") is False
    assert is_newer_version("", "1.0.1") is False


def test_fetch_latest_version_pypi_success() -> None:
    fake_body = json.dumps({"info": {"version": "1.2.3"}}).encode("utf-8")
    mock_response = io.BytesIO(fake_body)
    mock_response.status = 200  # type: ignore[attr-defined]

    with patch("urllib.request.urlopen", return_value=mock_response):
        version = fetch_latest_version("https://example.com/pypi.json")
    assert version == "1.2.3"


def test_fetch_latest_version_github_success() -> None:
    fake_body = json.dumps({"tag_name": "v2.0.0"}).encode("utf-8")
    mock_response = io.BytesIO(fake_body)
    mock_response.status = 200  # type: ignore[attr-defined]

    with patch("urllib.request.urlopen", return_value=mock_response):
        version = fetch_latest_version("https://example.com/github.json")
    assert version == "2.0.0"


def test_fetch_latest_version_network_errors() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        assert fetch_latest_version("https://example.com/timeout") is None

    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        assert fetch_latest_version("https://example.com/timeout") is None

    bad_json = io.BytesIO(b"not json")
    bad_json.status = 200  # type: ignore[attr-defined]
    with patch("urllib.request.urlopen", return_value=bad_json):
        assert fetch_latest_version("https://example.com/bad-json") is None


def test_read_and_write_version_cache(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache" / "version_check.json"

    # File does not exist yet
    ver, fresh = read_version_cache(cache_file)
    assert ver is None
    assert fresh is False

    # Write version
    write_version_cache(cache_file, "1.0.2", checked_at=1000.0)
    ver, fresh = read_version_cache(cache_file, ttl=50.0, now=1020.0)
    assert ver == "1.0.2"
    assert fresh is True

    # Read expired
    ver, fresh = read_version_cache(cache_file, ttl=50.0, now=1060.0)
    assert ver == "1.0.2"
    assert fresh is False

    # Corrupted cache
    cache_file.write_text("invalid json")
    ver, fresh = read_version_cache(cache_file)
    assert ver is None
    assert fresh is False


def test_write_cache_os_error(tmp_path: Path) -> None:
    # Writing to a path that cannot be created should not crash
    blocked_path = tmp_path / "file.txt"
    blocked_path.touch()
    # file.txt cannot be a directory
    invalid_cache = blocked_path / "sub" / "version.json"
    write_version_cache(invalid_cache, "1.0.2")


def test_should_check_for_updates() -> None:
    assert should_check_for_updates(env={}, enabled_flag=True) is True
    assert should_check_for_updates(env={}, enabled_flag=False) is False
    assert (
        should_check_for_updates(env={"UNICAL_NO_UPDATE_CHECK": "1"}, enabled_flag=True)
        is False
    )
    assert (
        should_check_for_updates(
            env={"UNICAL_NO_UPDATE_CHECK": "true"}, enabled_flag=True
        )
        is False
    )
    assert should_check_for_updates(env={"CI": "true"}, enabled_flag=True) is False
    assert should_check_for_updates(env={"CI": "1"}, enabled_flag=True) is False
    assert should_check_for_updates(env={"CI": "false"}, enabled_flag=True) is True


def test_check_for_updates_disabled(tmp_path: Path) -> None:
    res = check_for_updates(
        current_version="1.0.1",
        cache_path=tmp_path / "c.json",
        enabled=False,
    )
    assert res == VersionCheckResult(
        current_version="1.0.1",
        latest_version=None,
        has_update=False,
    )


def test_check_for_updates_cache_hit(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"
    write_version_cache(cache_file, "1.0.2", checked_at=100.0)

    with patch("unical.version_check.fetch_latest_version") as mock_fetch:
        res = check_for_updates(
            current_version="1.0.1",
            cache_path=cache_file,
            cache_ttl=500.0,
            now=150.0,
        )
    assert mock_fetch.call_count == 0
    assert res.has_update is True
    assert res.latest_version == "1.0.2"
    assert res.from_cache is True


def test_check_for_updates_cache_hit_same_version(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"
    write_version_cache(cache_file, "1.0.1", checked_at=100.0)

    res = check_for_updates(
        current_version="1.0.1",
        cache_path=cache_file,
        cache_ttl=500.0,
        now=150.0,
    )
    assert res.has_update is False
    assert res.latest_version == "1.0.1"
    assert res.from_cache is True


def test_check_for_updates_stale_cache_fetches_network(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"
    write_version_cache(cache_file, "1.0.1", checked_at=100.0)

    with patch(
        "unical.version_check.fetch_latest_version", return_value="1.0.3"
    ) as mock_fetch:
        res = check_for_updates(
            current_version="1.0.1",
            cache_path=cache_file,
            cache_ttl=50.0,
            now=200.0,
        )
    assert mock_fetch.call_count == 1
    assert res.has_update is True
    assert res.latest_version == "1.0.3"
    assert res.from_cache is False

    # Check that cache was updated with 1.0.3
    cached_ver, _ = read_version_cache(cache_file)
    assert cached_ver == "1.0.3"


def test_check_for_updates_force_bypasses_cache(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"
    write_version_cache(cache_file, "1.0.1", checked_at=100.0)

    with patch(
        "unical.version_check.fetch_latest_version", return_value="1.0.4"
    ) as mock_fetch:
        res = check_for_updates(
            current_version="1.0.1",
            cache_path=cache_file,
            cache_ttl=500.0,
            force=True,
            now=120.0,
        )
    assert mock_fetch.call_count == 1
    assert res.latest_version == "1.0.4"
    assert res.has_update is True
    assert res.from_cache is False


def test_check_for_updates_network_failure(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"

    with patch("unical.version_check.fetch_latest_version", return_value=None):
        res = check_for_updates(
            current_version="1.0.1",
            cache_path=cache_file,
            now=100.0,
        )
    assert res.has_update is False
    assert res.latest_version is None
    assert res.error is not None

    # Cached failure should prevent immediate retry within failure TTL
    with patch("unical.version_check.fetch_latest_version") as mock_fetch:
        res2 = check_for_updates(
            current_version="1.0.1",
            cache_path=cache_file,
            now=150.0,
        )
    assert mock_fetch.call_count == 0
    assert res2.has_update is False


def test_async_update_checker(tmp_path: Path) -> None:
    cache_file = tmp_path / "version.json"
    write_version_cache(cache_file, "1.0.5", checked_at=time.time())

    checker = AsyncUpdateChecker(
        enabled=True,
        cache_path=cache_file,
    )
    # Patch should_check_for_updates to True since test environment has PYTEST_CURRENT_TEST
    checker.enabled = True
    checker.start()
    res = checker.get_result(timeout=1.0)
    assert res is not None
    assert res.latest_version == "1.0.5"
    assert res.has_update is True


def test_async_update_checker_disabled() -> None:
    checker = AsyncUpdateChecker(enabled=False)
    checker.start()
    assert checker.get_result() is None


def test_format_upgrade_notice() -> None:
    notice_plain = format_upgrade_notice("1.0.1", "1.0.2", use_ansi=False)
    assert "1.0.1" in notice_plain
    assert "1.0.2" in notice_plain
    assert "pip install --upgrade uni-calendar-coloring" in notice_plain
    assert "https://github.com/Bert0ns/uni-calendar-coloring/releases" in notice_plain

    notice_ansi = format_upgrade_notice("1.0.1", "1.0.2", use_ansi=True)
    assert "\033[" in notice_ansi
    assert "1.0.1" in notice_ansi
    assert "1.0.2" in notice_ansi
