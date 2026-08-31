"""Fetch reading since the last run: discover HN/Reddit/X items, claim them
against the shared coordination API, resolve metadata, download articles
concurrently, write the index, then log a summary and optionally notify.

Coordination with other machines costs exactly 4 API calls per run,
regardless of item count: one /claim, two /complete flushes (after phase 1,
after phase 2), one /items. See docs/superpowers/specs/2026-08-29-multi-
machine-reading-queue-sync-design.md for why.

Phase 1 resolves metadata sequentially (rate-limit friendly); phase 2
downloads link-post articles concurrently with a Rich live table. Logging
appends a summary to ``cfg.log_path`` on every run; macOS notification and
email are inert until configured, so scheduling this via cron needs no setup
beyond what an interactive run already needs.
"""

import asyncio
import shutil
import smtplib
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

from archivore.clients import fetcher, hn, queue_api, reddit, x
from archivore.clients.browsers import get_all_history
from archivore.config import Config, config_summary, save_last_run
from archivore.models import (
    ClaimItem,
    ClaimResult,
    CompleteItem,
    ResolvedItem,
    RunResult,
)
from archivore.render import write_article_file, write_index
from archivore.sources import (
    extract_hn_items,
    extract_reddit_items,
    extract_x_items,
)
from archivore.timeutil import days_ago

console = Console()


def discover_items(cfg: Config, since: datetime) -> list[ClaimItem]:
    """Scan browser history since ``since`` and extract every HN/Reddit/X
    item — fully, in memory — before any coordination-API call happens."""
    history = get_all_history(since)
    items: list[ClaimItem] = []

    hn_items = extract_hn_items(history)
    console.print(f"  HN:     [cyan]{len(hn_items)}[/cyan] item(s)")
    for item_id in hn_items:
        items.append(
            ClaimItem(
                item_id=item_id,
                source="hn",
                comments_url=f"https://news.ycombinator.com/item?id={item_id}",
                article_url=None,
            )
        )

    reddit_items = extract_reddit_items(history, cfg.reddit_subreddits)
    console.print(f"  Reddit: [cyan]{len(reddit_items)}[/cyan] item(s)")
    for post_id, orig_url in reddit_items.items():
        # Original URL goes in comments_url so phase 1 can rebuild old.reddit
        items.append(
            ClaimItem(
                item_id=post_id,
                source="reddit",
                comments_url=orig_url,
                article_url=None,
            )
        )

    x_items = extract_x_items(history)
    console.print(f"  X:      [cyan]{len(x_items)}[/cyan] item(s)")
    for xid, (kind, orig_url) in x_items.items():
        # kind|orig_url goes in article_url until phase 1 resolves it; the x_
        # prefix keeps numeric tweet IDs from colliding with HN item IDs
        items.append(
            ClaimItem(
                item_id=f"x_{xid}",
                source="x",
                comments_url=orig_url,
                article_url=f"{kind}|{orig_url}",
            )
        )

    return items


def partition_claims(results: list[ClaimResult], max_retries: int) -> list[str]:
    """Return the item_ids this run should fetch: newly claimed items, plus
    previously-failed items still under the retry limit."""
    to_fetch = []
    for r in results:
        if r["claimed"]:
            to_fetch.append(r["item_id"])
        elif r["status"] == "failed" and r["retries"] < max_retries:
            to_fetch.append(r["item_id"])
    return to_fetch


def _resolve_one(item: ClaimItem, cfg: Config) -> ResolvedItem | None:
    """Dispatch one claimed item to its source client, honoring rate delays."""
    source = item["source"]
    if source == "hn":
        result = hn.resolve(item["item_id"])
        time.sleep(cfg.hn_delay)
    elif source == "reddit":
        result = reddit.resolve(item["item_id"], item["comments_url"])
        time.sleep(cfg.meta_delay)
    elif source == "x":
        stored = item["article_url"]
        kind, orig_url = (
            stored.split("|", 1) if stored else ("tweet", item["comments_url"])
        )
        result = x.resolve(item["item_id"].removeprefix("x_"), kind, orig_url)
        time.sleep(cfg.meta_delay)
    else:
        result = None
    return result


def phase1_resolve(
    to_fetch: list[ClaimItem], cfg: Config
) -> tuple[list[CompleteItem], list[dict]]:
    """Resolve metadata for every claimed item.

    Returns (phase-1 completions to report immediately, items ready for a
    phase-2 download). Self-posts are fully resolved here and never reach
    phase 2.
    """
    if not to_fetch:
        return [], []

    console.print(f"\n[bold]Phase 1[/bold] — resolving {len(to_fetch)} item(s)…")
    completions: list[CompleteItem] = []
    to_download: list[dict] = []

    for claim_item in to_fetch:
        item_id = claim_item["item_id"]
        console.print(f"  [{claim_item['source']}:{item_id}] ", end="")
        try:
            item = _resolve_one(claim_item, cfg)
            if item is None or not item.title:
                reason = (
                    "no title"
                    if item is not None
                    else f"unknown source '{claim_item['source']}'"
                )
                console.print(f"[yellow]{reason}, skipping[/yellow]")
                completions.append(
                    CompleteItem(
                        item_id=item_id,
                        status="skipped",
                        title=None,
                        is_selfpost=None,
                        filename=None,
                        last_error=reason,
                    )
                )
                continue

            console.print(item.title[:70])

            if item.is_selfpost and item.selftext is not None:
                filename = write_article_file(
                    cfg.output_dir,
                    item_id,
                    item.title,
                    item.article_url,
                    claim_item["comments_url"],
                    item.selftext,
                    "",
                )
                completions.append(
                    CompleteItem(
                        item_id=item_id,
                        status="done",
                        title=item.title,
                        is_selfpost=True,
                        filename=filename,
                        last_error=None,
                    )
                )
            else:
                to_download.append(
                    {
                        "item_id": item_id,
                        "source": claim_item["source"],
                        "title": item.title,
                        "article_url": item.article_url,
                        "comments_url": claim_item["comments_url"],
                    }
                )
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            completions.append(
                CompleteItem(
                    item_id=item_id,
                    status="failed",
                    title=None,
                    is_selfpost=None,
                    filename=None,
                    last_error=str(e),
                )
            )

    return completions, to_download


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


async def phase2_download(to_download: list[dict], cfg: Config) -> list[CompleteItem]:
    """Download all claimed link-post articles concurrently. Returns each
    item's outcome for the caller to report via queue_api.complete()."""
    if not to_download:
        console.print("\n[green]Nothing to download in phase 2.[/green]")
        return []

    rows_by_id = {item["item_id"]: item for item in to_download}
    state: fetcher.StateMap = {item["item_id"]: ("⏸", "queued") for item in to_download}
    completions: list[CompleteItem] = []

    console.print(
        f"\n[bold]Phase 2[/bold] — downloading {len(to_download)} article(s) "
        f"({cfg.concurrency} concurrent)…\n"
    )

    connector = aiohttp.TCPConnector(limit=cfg.concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(cfg.concurrency)

        async def bounded(item: dict) -> None:
            async with sem:
                result = await fetcher.fetch_article(
                    session, item, state, cfg.output_dir, cfg.max_retries
                )
                completions.append(result)

        with Live(
            _build_table(state, rows_by_id),
            refresh_per_second=4,
            console=console,
        ) as live:
            tasks = [asyncio.create_task(bounded(item)) for item in to_download]
            while not all(t.done() for t in tasks):
                live.update(_build_table(state, rows_by_id))
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks)
            live.update(_build_table(state, rows_by_id))

    return completions


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

    Scans history since the last successful run (tracked in the user
    config) rather than a fixed window. Coordination with other machines
    happens in exactly 4 API calls total, regardless of item count.
    """
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    run_start = datetime.now(timezone.utc)

    console.print("[bold]Config[/bold]")
    for key, value in config_summary(cfg).items():
        console.print(f"  {key} = {value}")
    console.print()

    since, why = _resolve_since(cfg, days_override)
    console.print(f"[bold]Scanning browser history[/bold] ({why})…")
    discovered = discover_items(cfg, since)
    console.print(f"Discovered [cyan]{len(discovered)}[/cyan] item(s) from history\n")

    by_id = {i["item_id"]: i for i in discovered}
    claim_results = queue_api.claim(cfg, discovered)
    to_fetch_ids = partition_claims(claim_results, cfg.max_retries)
    to_fetch = [by_id[iid] for iid in to_fetch_ids]
    console.print(f"Claimed [cyan]{len(to_fetch)}[/cyan] new/retryable item(s)\n")

    phase1_completions, to_download = phase1_resolve(to_fetch, cfg)
    queue_api.complete(cfg, phase1_completions)

    phase2_completions = asyncio.run(phase2_download(to_download, cfg))
    queue_api.complete(cfg, phase2_completions)

    all_items = queue_api.list_items(cfg)
    done_rows = [
        i for i in all_items if i["status"] in ("done", "skipped") and i["filename"]
    ]
    index_path, n = write_index(cfg.output_dir, done_rows, since, run_start)

    done = sum(1 for i in all_items if i["status"] == "done")
    skipped = sum(1 for i in all_items if i["status"] == "skipped")
    failed = sum(1 for i in all_items if i["status"] == "failed")
    retryable = sum(
        1
        for i in all_items
        if i["status"] == "failed" and i["retries"] < cfg.max_retries
    )

    new_items = [
        {"source": by_id[c["item_id"]]["source"], "title": c["title"]}
        for c in (*phase1_completions, *phase2_completions)
        if c["status"] == "done"
    ]

    console.print(
        f"\n[bold green]Done[/bold green] — "
        f"{done} saved, {skipped} skipped, {failed} failed"
    )
    if retryable:
        console.print(f"[yellow]{retryable} item(s) retryable — run again[/yellow]")
    console.print(f"Index: [cyan]{index_path}[/cyan]  ({n} articles)")

    if not skip_embed:
        run_qmd_embed()

    save_last_run(run_start.isoformat())

    return RunResult(
        new_queued=len(to_fetch),
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
