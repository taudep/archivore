"""Snapshot command: capture open tabs and history to SQLite / Markdown."""

from pathlib import Path

import click

from archivore.clients.browsers import (
    get_all_history,
    get_chrome_history,
    get_chrome_tabs,
    get_firefox_history,
    get_firefox_tabs,
)
from archivore.config import Config
from archivore.render import write_snapshot_markdown
from archivore.repository.snapshot import save_snapshot
from archivore.sources import dedupe_by_domain


def run(
    cfg: Config,
    db_path: Path | None,
    markdown_path: Path | None,
    days: int,
) -> None:
    """Collect tabs + history and write the requested outputs."""
    click.echo("Chrome tabs:")
    chrome_tabs = get_chrome_tabs()
    click.echo(f"  {len(chrome_tabs)} tab(s) found")

    click.echo("Firefox tabs:")
    firefox_tabs = get_firefox_tabs()
    click.echo(f"  {len(firefox_tabs)} tab(s) found")

    click.echo(f"\nChrome history (last {days} days):")
    chrome_history = get_chrome_history(days=days)
    click.echo(f"  {len(chrome_history)} URL(s) found")

    click.echo(f"Firefox history (last {days} days):")
    firefox_history = get_firefox_history(days=days)
    click.echo(f"  {len(firefox_history)} URL(s) found")

    n_raw_urls = len(chrome_history) + len(firefox_history)
    deduped = dedupe_by_domain(chrome_history + firefox_history, cfg.ignore_domains)
    click.echo(f"  → {len(deduped)} domains after dedup/filter")

    # Markdown row limit = unique domains active in the last 7 days
    if days <= 7:
        md_limit = len(deduped)
    else:
        click.echo("\nComputing 7-day domain count for markdown limit...")
        recent = get_all_history(days=7)
        md_limit = len(dedupe_by_domain(recent, cfg.ignore_domains))
        click.echo(f"  {md_limit} domains in last 7 days")

    if db_path is not None:
        n_new_tabs, n_upd_tabs, n_new_hist, n_upd_hist = save_snapshot(
            db_path, chrome_tabs, firefox_tabs, deduped
        )
        click.echo(f"\nSQLite → {db_path}")
        click.echo(f"  Tabs: {n_new_tabs} new, {n_upd_tabs} updated")
        click.echo(f"  History: {n_new_hist} new domains, {n_upd_hist} updated")

    if markdown_path is not None:
        write_snapshot_markdown(
            markdown_path,
            chrome_tabs,
            firefox_tabs,
            deduped,
            days,
            n_raw_urls,
            md_limit,
            cfg.ignore_domains,
        )
        shown = min(len(deduped), md_limit)
        click.echo(f"\nMarkdown → {markdown_path}")
        click.echo(
            f"  {len(chrome_tabs) + len(firefox_tabs)} tab(s), "
            f"{shown}/{len(deduped)} domains (limit: {md_limit} from last 7 days)"
        )

    if db_path is None and markdown_path is None:
        click.echo("\nNothing to do — pass --markdown and/or remove --no-db.")
