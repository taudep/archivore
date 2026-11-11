"""archivore command-line interface."""

from pathlib import Path

import click

from archivore import __version__
from archivore.config import load_config


@click.group()
@click.version_option(__version__, prog_name="archivore")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """archivore — personal web-reading capture and AI second brain."""
    ctx.obj = load_config()


@cli.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite output path (default: ~/tabs.db).",
)
@click.option("--no-db", is_flag=True, help="Skip SQLite output.")
@click.option(
    "--markdown",
    "markdown_path",
    type=click.Path(path_type=Path),
    is_flag=False,
    flag_value="__default__",
    default=None,
    help="Write a Markdown snapshot (default: md_path, date-prefixed).",
)
@click.option(
    "--days",
    type=int,
    default=None,
    metavar="N",
    help="Days of history to include (default: 90).",
)
@click.pass_obj
def snapshot(cfg, db_path, no_db, markdown_path, days) -> None:
    """Save open browser tabs and history to SQLite and/or Markdown."""
    from archivore.commands import snapshot as cmd

    if markdown_path == Path("__default__"):
        markdown_path = cmd.dated_markdown_path(cfg.md_path)
    cmd.run(
        cfg,
        db_path=None if no_db else (db_path or cfg.db_path),
        markdown_path=markdown_path,
        days=days or cfg.history_days,
    )


@cli.command()
@click.option(
    "--days",
    "days_override",
    type=int,
    default=None,
    metavar="N",
    help=(
        "Scan a fixed window of N days, ignoring the saved watermark "
        "(default: since the last run, or 7 days on first run)."
    ),
)
@click.option(
    "--skip-embed", is_flag=True, help="Skip the qmd index update at the end."
)
@click.pass_obj
def run(cfg, days_override, skip_embed) -> None:
    """Fetch reading since the last run (HN/Reddit/X) as Markdown + index.

    Scans since the saved watermark rather than a fixed schedule, appends a
    summary to the run log, and — if configured — sends a macOS notification
    and/or email, and runs a post-run shell command (``post_run_cmd``) when
    the scrape produced new files. All are inert until configured, so this
    is safe to run interactively or from cron without any extra setup.
    """
    from archivore.commands import run as cmd

    cmd.run(cfg, days_override=days_override, skip_embed=skip_embed)


def _http_url(ctx: click.Context, param: click.Parameter, value: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise click.BadParameter("must be an http(s) URL")
    return value.strip()


@cli.command()
@click.argument("url", callback=_http_url)
@click.option("--title", default=None, help="Title to use instead of the page's.")
@click.option("--note", default=None, help="Note to store in the front matter.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print exactly one JSON result line on stdout (for scripts).",
)
@click.pass_obj
def save(cfg, url, title, note, as_json) -> None:
    """Save one URL to the vault now, with multi-machine dedupe.

    Exit codes: 0 saved or duplicate, 1 failed (safe to retry), 2 usage
    error. Leaves the index and qmd embed to the next `archivore run`.
    """
    import json

    from rich.console import Console

    from archivore.commands import save as cmd

    # In --json mode stdout carries only the result line; progress → stderr.
    console = Console(stderr=as_json)
    result = cmd.save(cfg, url, title=title, note=note, console=console)

    if as_json:
        click.echo(json.dumps(result.to_dict()))
    elif result.status == "saved":
        console.print(f"[green]Saved[/green] → [cyan]{result.filename}[/cyan]")
        if result.error:
            console.print(f"[yellow]Note: page not fetched ({result.error})[/yellow]")
    elif result.status == "duplicate":
        console.print(f"[yellow]Duplicate[/yellow] — {result.item_id} already handled")
    else:
        console.print(f"[red]Failed[/red]: {result.error}")

    raise SystemExit(0 if result.status in ("saved", "duplicate") else 1)


if __name__ == "__main__":
    cli()
