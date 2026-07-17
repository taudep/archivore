"""Read open tabs and history from Chrome-family and Firefox browsers."""

import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archivore.models import HistoryRow, Tab

# Chrome timestamps are microseconds since 1601-01-01 (Windows FILETIME epoch)
_CHROME_EPOCH_DELTA_US = 11_644_473_600 * 1_000_000


def _warn(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def _chrome_time_to_iso(chrome_us: int) -> str:
    unix_s = (chrome_us - _CHROME_EPOCH_DELTA_US) / 1_000_000
    return datetime.fromtimestamp(unix_s, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Open tabs
# ---------------------------------------------------------------------------


def _applescript_tabs(app_name: str) -> list[Tab]:
    """Return open tabs from a Chromium-family browser via AppleScript.

    Uses tab-delimited output to avoid conflicts with colons in URLs and
    commas in titles that break AppleScript's native record syntax.
    """
    script = f"""
    tell application "{app_name}"
        set output to ""
        repeat with w in windows
            set wIndex to index of w
            repeat with t in tabs of w
                set output to output & wIndex & (ASCII character 9) & \
(URL of t) & (ASCII character 9) & (title of t) & (ASCII character 10)
            end repeat
        end repeat
        return output
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return []

    tabs: list[Tab] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            window_raw, url, title = parts
            try:
                window = int(window_raw)
            except ValueError:
                window = 1
            if url:
                tabs.append(Tab(url=url, title=title, window=window))
    return tabs


def get_chrome_tabs() -> list[Tab]:
    """Return open tabs from the first running Chrome-family browser."""
    for app in ("Google Chrome", "Chromium", "Google Chrome Canary"):
        tabs = _applescript_tabs(app)
        if tabs:
            return tabs
    return []


def _firefox_profiles_dir() -> Path | None:
    home = Path.home()
    candidates = [
        home / "Library/Application Support/Firefox/Profiles",  # macOS
        home / ".mozilla/firefox",  # Linux
        home / "AppData/Roaming/Mozilla/Firefox/Profiles",  # Windows
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _decode_mozlz4(path: Path) -> dict:
    try:
        import lz4.block
    except ImportError:
        raise ImportError("lz4 not installed — run: uv add lz4") from None

    data = path.read_bytes()
    if data[:8] != b"mozLz40\x00":
        raise ValueError(f"Not a mozLz4 file: {path}")

    original_size = struct.unpack("<I", data[8:12])[0]
    return json.loads(lz4.block.decompress(data[12:], uncompressed_size=original_size))


def _tabs_from_session(session: dict) -> list[Tab]:
    tabs: list[Tab] = []
    for w_idx, window in enumerate(session.get("windows", []), 1):
        for tab in window.get("tabs", []):
            entries = tab.get("entries", [])
            if not entries:
                continue
            idx = max(0, min(tab.get("index", len(entries)) - 1, len(entries) - 1))
            entry = entries[idx]
            url = entry.get("url", "")
            if url and not url.startswith("about:"):
                tabs.append(Tab(url=url, title=entry.get("title", ""), window=w_idx))
    return tabs


def get_firefox_tabs() -> list[Tab]:
    """Return open tabs from Firefox session-store files."""
    profiles_dir = _firefox_profiles_dir()
    if profiles_dir is None:
        return []

    patterns = [
        "*/sessionstore.jsonlz4",
        "*/sessionstore-backups/recovery.jsonlz4",
    ]
    tabs: list[Tab] = []

    for pattern in patterns:
        for session_file in sorted(profiles_dir.glob(pattern)):
            try:
                session = _decode_mozlz4(session_file)
                found = _tabs_from_session(session)
                if found:
                    tabs.extend(found)
                    break
            except ImportError as e:
                _warn(str(e))
                return []
            except Exception as e:
                _warn(f"Could not read {session_file.name}: {e}")
        if tabs:
            break

    return tabs


# ---------------------------------------------------------------------------
# Browser history
# ---------------------------------------------------------------------------


def _copy_db(src: Path) -> Path:
    """Copy a browser DB to a temp file so it can be read while locked."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    shutil.copy2(src, tmp.name)
    return Path(tmp.name)


def _chrome_history_paths() -> Iterator[Path]:
    """Yield History SQLite paths for all Chrome-family profiles."""
    base_dirs = [
        Path.home() / "Library/Application Support/Google/Chrome",  # macOS
        Path.home() / ".config/google-chrome",  # Linux
        Path.home() / "AppData/Local/Google/Chrome/User Data",  # Windows
        Path.home() / "Library/Application Support/Chromium",
        Path.home() / ".config/chromium",
    ]
    for base in base_dirs:
        if not base.exists():
            continue
        # Default profile and numbered profiles (Profile 1, Profile 2, …)
        for profile_dir in [base / "Default"] + list(base.glob("Profile *")):
            history = profile_dir / "History"
            if history.exists():
                yield history


def get_chrome_history(days: int = 90) -> list[HistoryRow]:
    """Return Chrome history rows newer than ``days``, merged across profiles."""
    cutoff_us = (
        _CHROME_EPOCH_DELTA_US
        + int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        * 1_000_000
    )

    rows: list[HistoryRow] = []
    for history_path in _chrome_history_paths():
        tmp = _copy_db(history_path)
        try:
            conn = sqlite3.connect(tmp)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT url, title, visit_count, last_visit_time
                FROM urls
                WHERE last_visit_time >= ?
                ORDER BY last_visit_time DESC
                """,
                (cutoff_us,),
            )
            for row in cur:
                rows.append(
                    HistoryRow(
                        url=row["url"],
                        title=row["title"] or "",
                        visit_count=row["visit_count"],
                        last_visited_at=_chrome_time_to_iso(row["last_visit_time"]),
                    )
                )
            conn.close()
        except Exception as e:
            _warn(f"Could not read Chrome history ({history_path.parent.name}): {e}")
        finally:
            tmp.unlink(missing_ok=True)

    # Merge duplicate URLs across profiles (keep highest visit count)
    merged: dict[str, HistoryRow] = {}
    for row in rows:
        url = row["url"]
        if url not in merged or row["visit_count"] > merged[url]["visit_count"]:
            merged[url] = row
    return list(merged.values())


def get_firefox_history(days: int = 90) -> list[HistoryRow]:
    """Return Firefox history rows newer than ``days`` across all profiles."""
    profiles_dir = _firefox_profiles_dir()
    if profiles_dir is None:
        return []

    cutoff_us = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1_000_000
    )

    rows: list[HistoryRow] = []
    for db_path in profiles_dir.glob("*/places.sqlite"):
        tmp = _copy_db(db_path)
        try:
            conn = sqlite3.connect(tmp)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT url, title, visit_count, last_visit_date
                FROM moz_places
                WHERE visit_count > 0
                  AND last_visit_date >= ?
                  AND url NOT LIKE 'place:%'
                ORDER BY last_visit_date DESC
                """,
                (cutoff_us,),
            )
            for row in cur:
                last_us = row["last_visit_date"] or 0
                last_iso = datetime.fromtimestamp(
                    last_us / 1_000_000, tz=timezone.utc
                ).isoformat()
                rows.append(
                    HistoryRow(
                        url=row["url"],
                        title=row["title"] or "",
                        visit_count=row["visit_count"],
                        last_visited_at=last_iso,
                    )
                )
            conn.close()
        except Exception as e:
            _warn(f"Could not read Firefox history: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    return rows


def get_all_history(days: int = 90) -> list[HistoryRow]:
    """Return combined Chrome + Firefox history for the last ``days``."""
    return get_chrome_history(days=days) + get_firefox_history(days=days)
