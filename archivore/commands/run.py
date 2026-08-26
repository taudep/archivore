"""Fetch reading since the last run: queue HN/Reddit/X items, resolve
metadata, download articles concurrently, write the index, then log a
summary and optionally notify.

Phase 1 resolves metadata sequentially (rate-limit friendly); phase 2
downloads link-post articles concurrently with a Rich live table. Logging
appends a summary to ``cfg.log_path`` on every run; macOS notification and
email are inert until configured, so scheduling this via cron needs no setup
beyond what an interactive run already needs.
"""

import asyncio
import shutil
import smtplib
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import aiohttp
from rich import box
from rich.console import Console
from rich.live import Live
from rich.table import Table

from archivore.clients import fetcher, hn, reddit, x
from archivore.clients.browsers import get_all_history
from archivore.config import Config, save_last_run
from archivore.models import ResolvedItem, RunResult
from archivore.render import write_article_file, write_index
from archivore.repository import queue
from archivore.sources import (
    extract_hn_items,
    extract_reddit_items,
    extract_x_items,
)
from archivore.timeutil import days_ago

console = Console()


def sync_all_sources(
    conn: sqlite3.Connection, cfg: Config, since: datetime, now: str
) -> int:
    """Scan browser history since ``since`` and queue new items from every
    source."""
    history = get_all_history(since)
    new = 0

    hn_items = extract_hn_items(history)
    console.print(f"  HN:     [cyan]{len(hn_items)}[/cyan] item(s)")
    for item_id in hn_items:
        comments_url = f"https://news.ycombinator.com/item?id={item_id}"
        new += queue.insert(conn, item_id, "hn", comments_url, now)

    reddit_items = extract_reddit_items(history, cfg.reddit_subreddits)
    console.print(f"  Reddit: [cyan]{len(reddit_items)}[/cyan] item(s)")
    for post_id, orig_url in reddit_items.items():
        # Original URL goes in comments_url so phase 1 can rebuild old.reddit
        new += queue.insert(conn, post_id, "reddit", orig_url, now)

    x_items = extract_x_items(history)
    console.print(f"  X:      [cyan]{len(x_items)}[/cyan] item(s)")
    for xid, (kind, orig_url) in x_items.items():
        # kind|orig_url goes in article_url until phase 1 resolves it; the x_
        # prefix keeps numeric tweet IDs from colliding with HN item IDs
        new += queue.insert(
            conn,
            f"x_{xid}",
            "x",
            orig_url,
            now,
            article_url=f"{kind}|{orig_url}",
        )

    conn.commit()
    return new


def _resolve_one(row: sqlite3.Row, cfg: Config) -> ResolvedItem | None:
    """Dispatch one queue row to its source client, honoring rate delays."""
    source = row["source"]
    if source == "hn":
        result = hn.resolve(row["item_id"])
        time.sleep(cfg.hn_delay)
    elif source == "reddit":
        result = reddit.resolve(row["item_id"], row["comments_url"])
        time.sleep(cfg.meta_delay)
    elif source == "x":
        stored = row["article_url"]
        kind, orig_url = (
            stored.split("|", 1) if stored else ("tweet", row["comments_url"])
        )
        result = x.resolve(row["item_id"].removeprefix("x_"), kind, orig_url)
        time.sleep(cfg.meta_delay)
    else:
        result = None
    return result


def phase1_resolve(conn: sqlite3.Connection, cfg: Config, now: str) -> None:
    """Resolve metadata for every queued item that still lacks a title."""
    rows = queue.unresolved(conn)
    if not rows:
        return

    console.print(f"\n[bold]Phase 1[/bold] — resolving {len(rows)} item(s)…")

    for row in rows:
        item_id = row["item_id"]
        console.print(f"  [{row['source']}:{item_id}] ", end="")
        try:
            item = _resolve_one(row, cfg)
            if item is None or not item.title:
                reason = (
                    "no title"
                    if item is not None
                    else f"unknown source '{row['source']}'"
                )
                console.print(f"[yellow]{reason}, skipping[/yellow]")
                queue.mark(conn, item_id, "skipped", now, last_error=reason)
                continue

            console.print(item.title[:70])

            if item.is_selfpost and item.selftext is not None:
                # Write the file now; no phase-2 fetch needed
                filename = write_article_file(
                    cfg.output_dir,
                    item_id,
                    item.title,
                    item.article_url,
                    row["comments_url"],
                    item.selftext,
                    "",
                )
                queue.mark(
                    conn,
                    item_id,
                    "done",
                    now,
                    title=item.title,
                    article_url=item.article_url,
                    is_selfpost=1,
                    filename=filename,
                )
            else:
                queue.set_metadata(
                    conn,
                    item_id,
                    item.title,
                    item.article_url,
                    item.is_selfpost,
                    now,
                )
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            queue.mark(conn, item_id, "failed", now, last_error=str(e))


def _build_table(state: fetcher.StateMap, rows_by_id: dict) -> Table:
    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        min_width=80,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("Source", width=8, no_wrap=True)
    table.add_column("Title", ratio=3)
    table.add_column("Status", ratio=2)
    color_map = {
        "✅": "green",
        "❌": "red",
        "⛔": "yellow",
        "⏳": "cyan",
        "🔄": "blue",
        "⚠": "yellow",
    }
    for item_id, (icon, status) in state.items():
        r = rows_by_id.get(item_id, {})
        title = (r.get("title") or "")[:50]
        color = color_map.get(icon, "white")
        table.add_row(icon, r.get("source", ""), title, f"[{color}]{status}[/]")
    return table


async def phase2_download(conn: sqlite3.Connection, cfg: Config, now: str) -> None:
    """Download all pending link-post articles concurrently."""
    rows = queue.downloadable(conn, cfg.max_retries)
    if not rows:
        console.print("\n[green]Nothing to download in phase 2.[/green]")
        return

    rows_by_id = {r["item_id"]: dict(r) for r in rows}
    state: fetcher.StateMap = {r["item_id"]: ("⏸", "queued") for r in rows}
    db_lock = asyncio.Lock()

    console.print(
        f"\n[bold]Phase 2[/bold] — downloading {len(rows)} article(s) "
        f"({cfg.concurrency} concurrent)…\n"
    )

    connector = aiohttp.TCPConnector(limit=cfg.concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(cfg.concurrency)

        async def bounded(row: sqlite3.Row) -> None:
            async with sem:
                await fetcher.fetch_article(
                    session,
                    row,
                    state,
                    db_lock,
                    conn,
                    now,
                    cfg.output_dir,
                    cfg.max_retries,
                )

        with Live(
            _build_table(state, rows_by_id),
            refresh_per_second=4,
            console=console,
        ) as live:
            tasks = [asyncio.create_task(bounded(r)) for r in rows]
            while not all(t.done() for t in tasks):
                live.update(_build_table(state, rows_by_id))
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks)
            live.update(_build_table(state, rows_by_id))


def run_qmd_embed() -> None:
    """Refresh the qmd semantic index if qmd is installed."""
    qmd = shutil.which("qmd")
    if not qmd:
        console.print("[yellow]qmd not found in PATH — skipping embed[/yellow]")
        return
    console.print("\n[bold]Updating qmd index…[/bold]")
    result = subprocess.run([qmd, "embed"], capture_output=True, text=True)
    if result.returncode == 0:
        summary = next(
            (ln for ln in reversed(result.stdout.splitlines()) if ln.strip()),
            "qmd embed complete",
        )
        console.print(f"[green]{summary}[/green]")
    else:
        console.print(f"[yellow]qmd embed exited {result.returncode}[/yellow]")
        if result.stderr:
            console.print(result.stderr[:300])


def _resolve_since(cfg: Config, days_override: int | None) -> tuple[datetime, str]:
    """Pick the history cutoff and a description of why it was chosen."""
    if days_override is not None:
        return days_ago(days_override), f"last {days_override} days (override)"
    if cfg.last_run:
        since = datetime.fromisoformat(cfg.last_run)
        return since, f"since last run: {since.strftime('%Y-%m-%d %H:%M UTC')}"
    return (
        days_ago(cfg.digest_days),
        f"last {cfg.digest_days} days (no previous run recorded)",
    )


def _pipeline(
    cfg: Config, days_override: int | None = None, skip_embed: bool = False
) -> RunResult:
    """Execute the full fetch pipeline and return its outcome.

    Scans history since the last successful run (tracked in the user config)
    rather than a fixed window, so runs aren't tied to any particular
    schedule. Pass ``days_override`` to scan a fixed window instead, e.g. for
    a backfill.
    """
    cfg.output_dir.mkdir(exist_ok=True)
    conn = queue.open_queue(cfg.output_dir / "queue.db")
    run_start = datetime.now(timezone.utc)
    now = run_start.isoformat()

    since, why = _resolve_since(cfg, days_override)
    console.print(f"[bold]Scanning browser history[/bold] ({why})…")
    new = sync_all_sources(conn, cfg, since, now)
    console.print(f"Queued [cyan]{new}[/cyan] new item(s)\n")

    phase1_resolve(conn, cfg, now)
    asyncio.run(phase2_download(conn, cfg, now))

    index_path, n = write_index(
        cfg.output_dir, queue.index_rows(conn), since, run_start
    )

    done, skipped, failed, retryable = queue.status_counts(conn, cfg.max_retries)
    new_items = [dict(r) for r in queue.done_this_run(conn, now)]
    console.print(
        f"\n[bold green]Done[/bold green] — "
        f"{done} saved, {skipped} skipped, {failed} failed"
    )
    if retryable:
        console.print(f"[yellow]{retryable} item(s) retryable — run again[/yellow]")
    console.print(f"Index: [cyan]{index_path}[/cyan]  ({n} articles)")

    if not skip_embed:
        run_qmd_embed()

    save_last_run(now)
    conn.close()

    return RunResult(
        new_queued=new,
        done=done,
        skipped=skipped,
        failed=failed,
        retryable=retryable,
        index_path=index_path,
        article_count=n,
        new_items=new_items,
    )


def _summary_lines(result: RunResult) -> list[str]:
    lines = [
        f"queued={result.new_queued} done={result.done} skipped={result.skipped} "
        f"failed={result.failed} retryable={result.retryable}",
        f"index: {result.index_path} ({result.article_count} articles)",
    ]
    if result.new_items:
        lines.append("new articles:")
        lines += [f"  [{item['source']}] {item['title']}" for item in result.new_items]
    return lines


def _write_log(log_path: Path, started: datetime, result: RunResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"=== {started.strftime('%Y-%m-%d %H:%M:%S UTC')} ==="
    block = "\n".join([header, *_summary_lines(result), ""])
    with log_path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")


def _notify_macos(result: RunResult) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    message = f"{result.done} saved, {result.skipped} skipped, {result.failed} failed"
    script = f'display notification "{message}" with title "Archivore"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
    except OSError as e:
        console.print(f"[yellow]macOS notification failed: {e}[/yellow]")


def _send_email(cfg: Config, result: RunResult) -> None:
    subject = (
        f"Archivore run: {result.done} saved"
        f"{f', {result.failed} failed' if result.failed else ''}"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_from or cfg.smtp_user
    msg["To"] = cfg.email_to
    msg.set_content("\n".join(_summary_lines(result)))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if cfg.smtp_user and cfg.smtp_password:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        console.print(f"[yellow]Email notification failed: {e}[/yellow]")


def run(
    cfg: Config, days_override: int | None = None, skip_embed: bool = False
) -> None:
    """Run the fetch pipeline, then log a summary and notify if configured."""
    started = datetime.now(timezone.utc)
    result = _pipeline(cfg, days_override=days_override, skip_embed=skip_embed)

    _write_log(cfg.log_path, started, result)
    console.print(f"Logged run summary to [cyan]{cfg.log_path}[/cyan]")

    if cfg.notify_macos:
        _notify_macos(result)

    if cfg.smtp_host and cfg.email_to:
        _send_email(cfg, result)
