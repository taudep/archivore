#!/usr/bin/env python3
"""Save open browser tabs and history to a SQLite database and/or Markdown file."""

import argparse
import json
import shutil
import sqlite3
import struct
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse


DB_PATH = Path.home() / "tabs.db"
MD_PATH = Path.home() / "tabs.md"

# How many days of history to import
HISTORY_DAYS = 90

# Domains excluded from the Markdown history table (high-volume noise)
IGNORE_DOMAINS = {
    "gmail.com",
    "mail.google.com",
    "amazon.com",
    "facebook.com",
}

# Chrome timestamps are microseconds since 1601-01-01 (Windows FILETIME epoch)
_CHROME_EPOCH_DELTA_US = 11_644_473_600 * 1_000_000


def _chrome_time_to_iso(chrome_us):
    unix_s = (chrome_us - _CHROME_EPOCH_DELTA_US) / 1_000_000
    return datetime.fromtimestamp(unix_s, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Open tabs
# ---------------------------------------------------------------------------

def _applescript_tabs(app_name):
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
                set output to output & wIndex & (ASCII character 9) & (URL of t) & (ASCII character 9) & (title of t) & (ASCII character 10)
            end repeat
        end repeat
        return output
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return []

    tabs = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            window_raw, url, title = parts
            try:
                window = int(window_raw)
            except ValueError:
                window = 1
            if url:
                tabs.append({"url": url, "title": title, "window": window})
    return tabs


def get_chrome_tabs():
    for app in ("Google Chrome", "Chromium", "Google Chrome Canary"):
        tabs = _applescript_tabs(app)
        if tabs:
            return tabs
    return []


def _firefox_profiles_dir():
    home = Path.home()
    candidates = [
        home / "Library/Application Support/Firefox/Profiles",  # macOS
        home / ".mozilla/firefox",                               # Linux
        home / "AppData/Roaming/Mozilla/Firefox/Profiles",      # Windows
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _decode_mozlz4(path):
    try:
        import lz4.block
    except ImportError:
        raise ImportError("lz4 not installed — run: pip install lz4")

    with open(path, "rb") as f:
        data = f.read()

    if data[:8] != b"mozLz40\x00":
        raise ValueError(f"Not a mozLz4 file: {path}")

    original_size = struct.unpack("<I", data[8:12])[0]
    return json.loads(lz4.block.decompress(data[12:], uncompressed_size=original_size))


def _tabs_from_session(session):
    tabs = []
    for w_idx, window in enumerate(session.get("windows", []), 1):
        for tab in window.get("tabs", []):
            entries = tab.get("entries", [])
            if not entries:
                continue
            idx = max(0, min(tab.get("index", len(entries)) - 1, len(entries) - 1))
            entry = entries[idx]
            url = entry.get("url", "")
            if url and not url.startswith("about:"):
                tabs.append({"url": url, "title": entry.get("title", ""), "window": w_idx})
    return tabs


def get_firefox_tabs():
    profiles_dir = _firefox_profiles_dir()
    if profiles_dir is None:
        return []

    patterns = [
        "*/sessionstore.jsonlz4",
        "*/sessionstore-backups/recovery.jsonlz4",
    ]
    tabs = []
    lz4_missing_reported = False

    for pattern in patterns:
        for session_file in sorted(profiles_dir.glob(pattern)):
            try:
                session = _decode_mozlz4(session_file)
                found = _tabs_from_session(session)
                if found:
                    tabs.extend(found)
                    break
            except ImportError as e:
                if not lz4_missing_reported:
                    print(f"  {e}")
                    lz4_missing_reported = True
                return []
            except Exception as e:
                print(f"  Could not read {session_file.name}: {e}")
        if tabs:
            break

    return tabs


# ---------------------------------------------------------------------------
# Browser history
# ---------------------------------------------------------------------------

def _copy_db(src):
    """Copy a browser DB to a temp file so we can read it while the browser holds the lock."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    shutil.copy2(src, tmp.name)
    return Path(tmp.name)


def _chrome_history_paths():
    """Yield History SQLite paths for all Chrome-family profiles."""
    base_dirs = [
        Path.home() / "Library/Application Support/Google/Chrome",   # macOS
        Path.home() / ".config/google-chrome",                        # Linux
        Path.home() / "AppData/Local/Google/Chrome/User Data",        # Windows
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


def get_chrome_history(days=HISTORY_DAYS):
    cutoff_us = (
        _CHROME_EPOCH_DELTA_US
        + int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp()) * 1_000_000
    )

    rows = []
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
                rows.append({
                    "url": row["url"],
                    "title": row["title"] or "",
                    "visit_count": row["visit_count"],
                    "last_visited_at": _chrome_time_to_iso(row["last_visit_time"]),
                })
            conn.close()
        except Exception as e:
            print(f"  Could not read Chrome history ({history_path.parent.name}): {e}")
        finally:
            tmp.unlink(missing_ok=True)

    # Merge duplicate URLs across profiles (keep highest visit count)
    merged = {}
    for row in rows:
        url = row["url"]
        if url not in merged or row["visit_count"] > merged[url]["visit_count"]:
            merged[url] = row
    return list(merged.values())


def _firefox_history_paths():
    profiles_dir = _firefox_profiles_dir()
    if profiles_dir is None:
        return []
    return list(profiles_dir.glob("*/places.sqlite"))


def get_firefox_history(days=HISTORY_DAYS):
    cutoff_us = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1_000_000
    )

    rows = []
    for db_path in _firefox_history_paths():
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
                rows.append({
                    "url": row["url"],
                    "title": row["title"] or "",
                    "visit_count": row["visit_count"],
                    "last_visited_at": last_iso,
                })
            conn.close()
        except Exception as e:
            print(f"  Could not read Firefox history: {e}")
        finally:
            tmp.unlink(missing_ok=True)

    return rows


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def setup_db(conn):
    # Migrate tabs: old schema had snapshot_id, no UNIQUE constraint
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tabs'").fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tabs)").fetchall()}
        if "snapshot_id" in cols:
            conn.execute("DROP TABLE tabs")

    # Migrate history: old schema was snapshot-scoped per raw URL, not domain-deduped
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history'").fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(history)").fetchall()}
        if "snapshot_id" in cols or "domain" not in cols:
            conn.execute("DROP TABLE history")

    # snapshots table is no longer used; drop it if it exists
    conn.execute("DROP TABLE IF EXISTS snapshots")
    conn.commit()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tabs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            browser       TEXT NOT NULL,
            window        INTEGER NOT NULL DEFAULT 1,
            url           TEXT NOT NULL,
            title         TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            UNIQUE(browser, url)
        );

        CREATE TABLE IF NOT EXISTS history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT NOT NULL,
            url             TEXT NOT NULL,
            title           TEXT,
            visit_count     INTEGER NOT NULL DEFAULT 1,
            last_visited_at TEXT NOT NULL,
            first_seen_at   TEXT NOT NULL,
            UNIQUE(domain)
        );

        CREATE INDEX IF NOT EXISTS idx_tabs_browser   ON tabs(browser);
        CREATE INDEX IF NOT EXISTS idx_tabs_last_seen ON tabs(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_history_visits ON history(visit_count);
    """)


def save_data(chrome_tabs, firefox_tabs, deduped_history):
    """Persist tabs and domain-deduped history to SQLite, upserting on conflict."""
    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)

    now = datetime.now(timezone.utc).isoformat()

    # Tabs: UPSERT by (browser, url)
    tab_rows = [
        ("chrome", t["window"], t["url"], t["title"], now, now)
        for t in chrome_tabs
    ] + [
        ("firefox", t["window"], t["url"], t["title"], now, now)
        for t in firefox_tabs
    ]
    n_tabs_before = conn.execute("SELECT COUNT(*) FROM tabs").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO tabs (browser, window, url, title, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(browser, url) DO UPDATE SET
            window       = excluded.window,
            title        = excluded.title,
            last_seen_at = excluded.last_seen_at
        """,
        tab_rows,
    )
    n_tabs_after = conn.execute("SELECT COUNT(*) FROM tabs").fetchone()[0]
    n_new_tabs = n_tabs_after - n_tabs_before
    n_updated_tabs = len(tab_rows) - n_new_tabs

    # History: UPSERT by domain; refresh counts and best URL, preserve first_seen_at
    history_rows = [
        (h["domain"], h["url"], h["title"], h["visit_count"], h["last_visited_at"], now)
        for h in deduped_history
    ]
    n_hist_before = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO history (domain, url, title, visit_count, last_visited_at, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            url             = excluded.url,
            title           = excluded.title,
            visit_count     = excluded.visit_count,
            last_visited_at = excluded.last_visited_at
        """,
        history_rows,
    )
    n_hist_after = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    n_new_hist = n_hist_after - n_hist_before
    n_updated_hist = len(history_rows) - n_new_hist

    conn.commit()
    conn.close()
    return n_new_tabs, n_updated_tabs, n_new_hist, n_updated_hist


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def _dedupe_history(chrome_history, firefox_history):
    """Combine browser histories, group by domain, filter noise, sort by total visits.

    Applied before both SQLite writes and Markdown output, so both reflect the
    same domain-level view. For each domain we sum all visit counts and surface
    the single most-visited URL as the representative link.
    """
    all_rows = chrome_history + firefox_history

    # key: bare hostname (www. stripped); value: aggregated stats
    domains = {}
    for h in all_rows:
        parsed = urlparse(h["url"])
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").removeprefix("www.")
        if not host or host in IGNORE_DOMAINS:
            continue

        if host not in domains:
            domains[host] = {
                "visit_count": 0,
                "best_url": h["url"],
                "best_title": h["title"],
                "best_count": 0,
                "last_visited_at": h["last_visited_at"],
            }

        d = domains[host]
        d["visit_count"] += h["visit_count"]

        if h["visit_count"] > d["best_count"]:
            d["best_count"] = h["visit_count"]
            d["best_url"] = h["url"]
            d["best_title"] = h["title"]

        if h["last_visited_at"] > d["last_visited_at"]:
            d["last_visited_at"] = h["last_visited_at"]

    result = [
        {
            "domain": host,
            "url": d["best_url"],
            "title": d["best_title"],
            "visit_count": d["visit_count"],
            "last_visited_at": d["last_visited_at"],
        }
        for host, d in domains.items()
    ]
    result.sort(key=lambda r: r["visit_count"], reverse=True)
    return result


def _md_escape(text):
    """Escape pipe characters so they don't break Markdown tables."""
    return str(text).replace("|", "\\|")


def write_markdown(path, chrome_tabs, firefox_tabs, deduped_history, days, n_raw_urls, limit):
    now = datetime.now(timezone.utc)
    lines = [
        f"# Browser Snapshot — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # --- Open tabs -----------------------------------------------------------
    all_tabs = (
        [("Chrome", t) for t in chrome_tabs]
        + [("Firefox", t) for t in firefox_tabs]
    )
    lines += [f"## Open Tabs ({len(all_tabs)})", ""]

    if all_tabs:
        from itertools import groupby
        keyfn = lambda bt: (bt[0], bt[1]["window"])
        for (browser, window), group in groupby(all_tabs, key=keyfn):
            lines.append(f"### {browser} — Window {window}")
            lines.append("")
            for _, tab in group:
                title = tab["title"] or tab["url"]
                lines.append(f"- [{_md_escape(title)}]({tab['url']})")
            lines.append("")
    else:
        lines += ["_No open tabs found._", ""]

    # --- History -------------------------------------------------------------
    shown = deduped_history[:limit]
    total_domains = len(deduped_history)
    caption = f"{len(shown)} domains"
    if total_domains > len(shown):
        caption += f" of {total_domains:,}"
    caption += f", from {n_raw_urls:,} raw URLs"
    ignored = ", ".join(sorted(IGNORE_DOMAINS))
    lines += [
        f"## History — Last {days} Days ({caption})",
        f"_Ignored: {ignored}_",
        "",
    ]

    if shown:
        lines.append("| Visits | Last Visited | Domain | Top Page |")
        lines.append("|-------:|-------------|--------|---------|")
        for h in shown:
            date = h["last_visited_at"][:10]
            title = _md_escape(h["title"] or h["domain"])
            lines.append(f"| {h['visit_count']:,} | {date} | {h['domain']} | [{title}]({h['url']}) |")
        lines.append("")
    else:
        lines += ["_No history found._", ""]

    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Save open browser tabs and history to SQLite and/or Markdown."
    )
    parser.add_argument(
        "--db",
        metavar="FILE",
        nargs="?",
        const=str(DB_PATH),
        default=str(DB_PATH),
        help=f"SQLite output path (default: {DB_PATH}). Pass --no-db to skip.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip SQLite output.",
    )
    parser.add_argument(
        "--markdown",
        metavar="FILE",
        nargs="?",
        const=str(MD_PATH),
        help=f"Write Markdown snapshot (default path: {MD_PATH}).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=HISTORY_DAYS,
        metavar="N",
        help=f"Days of history to include (default: {HISTORY_DAYS}).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("Chrome tabs:")
    chrome_tabs = get_chrome_tabs()
    print(f"  {len(chrome_tabs)} tab(s) found")

    print("Firefox tabs:")
    firefox_tabs = get_firefox_tabs()
    print(f"  {len(firefox_tabs)} tab(s) found")

    print(f"\nChrome history (last {args.days} days):")
    chrome_history = get_chrome_history(days=args.days)
    print(f"  {len(chrome_history)} URL(s) found")

    print(f"Firefox history (last {args.days} days):")
    firefox_history = get_firefox_history(days=args.days)
    print(f"  {len(firefox_history)} URL(s) found")

    n_raw_urls = len(chrome_history) + len(firefox_history)
    deduped_history = _dedupe_history(chrome_history, firefox_history)
    print(f"  → {len(deduped_history)} domains after dedup/filter")

    # Markdown row limit = unique domains active in the last 7 days
    if args.days <= 7:
        md_limit = len(deduped_history)
    else:
        print("\nComputing 7-day domain count for markdown limit...")
        c7 = get_chrome_history(days=7)
        f7 = get_firefox_history(days=7)
        md_limit = len(_dedupe_history(c7, f7))
        print(f"  {md_limit} domains in last 7 days")

    if not args.no_db:
        db_path = Path(args.db)
        global DB_PATH
        DB_PATH = db_path
        n_new_tabs, n_updated_tabs, n_new_hist, n_updated_hist = save_data(
            chrome_tabs, firefox_tabs, deduped_history
        )
        print(f"\nSQLite → {db_path}")
        print(f"  Tabs: {n_new_tabs} new, {n_updated_tabs} updated")
        print(f"  History: {n_new_hist} new domains, {n_updated_hist} updated")

    if args.markdown:
        md_path = Path(args.markdown)
        write_markdown(md_path, chrome_tabs, firefox_tabs, deduped_history, args.days, n_raw_urls, md_limit)
        shown = min(len(deduped_history), md_limit)
        print(f"\nMarkdown → {md_path}")
        print(f"  {len(chrome_tabs) + len(firefox_tabs)} tab(s), {shown}/{len(deduped_history)} domains (limit: {md_limit} from last 7 days)")

    if args.no_db and not args.markdown:
        print("\nNothing to do — pass --markdown and/or remove --no-db.")


if __name__ == "__main__":
    main()
