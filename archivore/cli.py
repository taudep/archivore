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
    help="Write a Markdown snapshot (default path: ~/tabs.md).",
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
        markdown_path = cfg.md_path
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
    and/or email. Both are inert until configured, so this is safe to run
    interactively or from cron without any extra setup.
    """
    from archivore.commands import run as cmd

    cmd.run(cfg, days_override=days_override, skip_embed=skip_embed)


if __name__ == "__main__":
    cli()
