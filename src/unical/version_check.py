"""Version detection and update notification for unical."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from unical import __version__
from unical.cli.ansi import BOLD, CYAN, GREEN, YELLOW, style

PACKAGE_NAME = "uni-calendar-coloring"
DEFAULT_PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
DEFAULT_TIMEOUT_SECONDS = 1.5
DEFAULT_CACHE_TTL_SECONDS = 86400.0  # 24 hours
FAILURE_CACHE_TTL_SECONDS = 3600.0  # 1 hour backoff on failure


def default_cache_dir() -> Path:
    """Standard user cache directory for unical."""
    custom_dir = os.getenv("UNICAL_CACHE_DIR")
    if custom_dir:
        return Path(custom_dir)
    return platformdirs.user_cache_path("unical", appauthor=False)


def default_version_cache_path() -> Path:
    """Default location for the version check cache file."""
    return default_cache_dir() / "version_check.json"


def is_newer_version(latest: str, current: str = __version__) -> bool:
    """Return True if `latest` is strictly newer than `current`.

    Uses PEP 440 parsing via `packaging.version.Version` when available,
    with a robust tuple-based fallback.
    """
    clean_latest = latest.strip().lstrip("v")
    clean_current = current.strip().lstrip("v")
    if not clean_latest or not clean_current:
        return False

    try:
        from packaging.version import Version

        v_latest = Version(clean_latest)
        v_current = Version(clean_current)
        return v_latest > v_current
    except Exception:

        def _parse(v: str) -> tuple[int, ...] | None:
            clean = v.split("+")[0].split("-")[0]
            parts: list[int] = []
            for segment in clean.split("."):
                digits = "".join(c for c in segment if c.isdigit())
                if not digits:
                    return None
                parts.append(int(digits))
            return tuple(parts) if parts else None

        t_latest = _parse(clean_latest)
        t_current = _parse(clean_current)
        if t_latest is None or t_current is None:
            return False
        return t_latest > t_current


def fetch_latest_version(
    url: str = DEFAULT_PYPI_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Fetch the latest released version string from PyPI or compatible JSON endpoint.

    Returns None on network timeout, connection error, or invalid payload.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"unical/{__version__}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                raw = response.read().decode("utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    # Standard PyPI schema: {"info": {"version": "..."}}
                    info = data.get("info")
                    if isinstance(info, dict) and "version" in info:
                        return str(info["version"]).strip()
                    # GitHub Releases schema: {"tag_name": "v..."}
                    if "tag_name" in data:
                        return str(data["tag_name"]).strip().lstrip("v")
    except Exception:
        return None
    return None


@dataclass(frozen=True)
class VersionCheckResult:
    current_version: str
    latest_version: str | None
    has_update: bool
    checked_at: float | None = None
    from_cache: bool = False
    error: str | None = None


def read_version_cache(
    cache_path: Path,
    ttl: float = DEFAULT_CACHE_TTL_SECONDS,
    failure_ttl: float = FAILURE_CACHE_TTL_SECONDS,
    now: float | None = None,
) -> tuple[str | None, bool]:
    """Read version check cache.

    Returns (cached_version, is_fresh).
    `cached_version` can be empty string if the last check failed.
    """
    if not cache_path.is_file():
        return None, False

    current_time = now if now is not None else time.time()
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, False
        checked_at = float(data.get("checked_at", 0))
        version: str | None = data.get("latest_version")
        effective_ttl = ttl if version else failure_ttl
        is_fresh = (current_time - checked_at) < effective_ttl
        return version, is_fresh
    except Exception:
        return None, False


def write_version_cache(
    cache_path: Path,
    latest_version: str | None,
    checked_at: float | None = None,
) -> None:
    """Save version check result to cache file."""
    timestamp = checked_at if checked_at is not None else time.time()
    payload = {
        "latest_version": latest_version or "",
        "checked_at": timestamp,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def should_check_for_updates(
    env: Mapping[str, str] | None = None,
    enabled_flag: bool = True,
) -> bool:
    """Determine whether an update check should be performed."""
    if not enabled_flag:
        return False
    environment = os.environ if env is None else env
    no_check = environment.get("UNICAL_NO_UPDATE_CHECK", "").strip().lower()
    if no_check in ("1", "true", "yes", "on"):
        return False
    ci = environment.get("CI", "").strip().lower()
    return not (ci and ci not in ("0", "false"))


def check_for_updates(
    current_version: str = __version__,
    cache_path: Path | None = None,
    cache_ttl: float = DEFAULT_CACHE_TTL_SECONDS,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    force: bool = False,
    enabled: bool = True,
    now: float | None = None,
) -> VersionCheckResult:
    """Check if a newer version of unical is available.

    Leverages local caching to avoid network latency on frequent invocations.
    """
    if not enabled or not should_check_for_updates():
        return VersionCheckResult(
            current_version=current_version,
            latest_version=None,
            has_update=False,
        )

    cache_file = cache_path or default_version_cache_path()
    request_url = url or os.getenv("UNICAL_CHECK_UPDATE_URL") or DEFAULT_PYPI_URL
    current_time = now if now is not None else time.time()

    if not force:
        cached_ver, is_fresh = read_version_cache(
            cache_file, ttl=cache_ttl, now=current_time
        )
        if is_fresh:
            if cached_ver:
                has_update = is_newer_version(cached_ver, current_version)
                return VersionCheckResult(
                    current_version=current_version,
                    latest_version=cached_ver,
                    has_update=has_update,
                    checked_at=current_time,
                    from_cache=True,
                )
            # Fresh failure cache: skip network hit
            return VersionCheckResult(
                current_version=current_version,
                latest_version=None,
                has_update=False,
                checked_at=current_time,
                from_cache=True,
            )

    latest = fetch_latest_version(url=request_url, timeout=timeout)
    if latest:
        write_version_cache(cache_file, latest, checked_at=current_time)
        has_update = is_newer_version(latest, current_version)
        return VersionCheckResult(
            current_version=current_version,
            latest_version=latest,
            has_update=has_update,
            checked_at=current_time,
            from_cache=False,
        )

    # Record failed attempt so offline commands don't hang every time
    write_version_cache(cache_file, "", checked_at=current_time)
    return VersionCheckResult(
        current_version=current_version,
        latest_version=None,
        has_update=False,
        checked_at=current_time,
        from_cache=False,
        error="Could not connect to update server",
    )


class AsyncUpdateChecker:
    """Runs version check in a background daemon thread to avoid blocking CLI work."""

    def __init__(
        self,
        enabled: bool = True,
        force: bool = False,
        cache_path: Path | None = None,
        url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.enabled = enabled and should_check_for_updates()
        self.force = force
        self.cache_path = cache_path
        self.url = url
        self.timeout = timeout
        self._result: VersionCheckResult | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background check."""
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._result = check_for_updates(
                cache_path=self.cache_path,
                url=self.url,
                timeout=self.timeout,
                force=self.force,
                enabled=self.enabled,
            )
        except Exception:
            self._result = None

    def get_result(self, timeout: float = 0.25) -> VersionCheckResult | None:
        """Return the result, waiting up to `timeout` seconds if thread is still running."""
        if not self.enabled:
            return None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        return self._result


def _supports_utf8() -> bool:
    try:
        encoding = getattr(sys.stdout, "encoding", "") or ""
        return "utf" in encoding.lower()
    except Exception:
        return True


def format_upgrade_notice(
    current_version: str,
    latest_version: str,
    *,
    use_ansi: bool = True,
) -> str:
    """Format an eye-catching box notifying the user of an available update."""
    utf8 = _supports_utf8()
    arrow = "→" if utf8 else "->"
    tl = "┌" if utf8 else "+"
    tr = "┐" if utf8 else "+"
    bl = "└" if utf8 else "+"
    br = "┘" if utf8 else "+"
    hz = "─" if utf8 else "-"
    vt = "│" if utf8 else "|"

    line1_plain = f"  A new version of unical is available: {current_version} {arrow} {latest_version}  "
    line2_plain = "  To upgrade, run: pip install --upgrade uni-calendar-coloring  "
    line3_plain = (
        "  Release notes: https://github.com/Bert0ns/uni-calendar-coloring/releases  "
    )

    width = max(len(line1_plain), len(line2_plain), len(line3_plain))
    top = f"{tl}{hz * width}{tr}"
    bottom = f"{bl}{hz * width}{br}"

    def pad(text: str) -> str:
        return text + " " * (width - len(text))

    if use_ansi:
        styled_top = style(top, YELLOW)
        styled_bottom = style(bottom, YELLOW)
        l1 = (
            style(vt, YELLOW)
            + "  A new version of unical is available: "
            + style(current_version, CYAN)
            + f" {arrow} "
            + style(latest_version, GREEN, BOLD)
            + " " * (width - len(line1_plain))
            + "  "
            + style(vt, YELLOW)
        )
        l2 = (
            style(vt, YELLOW)
            + "  To upgrade, run: "
            + style("pip install --upgrade uni-calendar-coloring", BOLD)
            + " " * (width - len(line2_plain))
            + "  "
            + style(vt, YELLOW)
        )
        l3 = (
            style(vt, YELLOW)
            + "  Release notes: "
            + style("https://github.com/Bert0ns/uni-calendar-coloring/releases", CYAN)
            + " " * (width - len(line3_plain))
            + "  "
            + style(vt, YELLOW)
        )
        return "\n".join([styled_top, l1, l2, l3, styled_bottom])

    l1 = f"{vt}{pad(line1_plain)}{vt}"
    l2 = f"{vt}{pad(line2_plain)}{vt}"
    l3 = f"{vt}{pad(line3_plain)}{vt}"
    return "\n".join([top, l1, l2, l3, bottom])
