"""HTTP client for the archivore-queue coordination API (Cloudflare Worker
+ D1). claim() and complete() are batch-only — one call per run, never one
call per item; see docs/superpowers/specs/2026-08-29-multi-machine-reading-
queue-sync-design.md."""

import requests

from archivore.config import Config
from archivore.models import ClaimItem, ClaimResult, CompleteItem


def _headers(cfg: Config) -> dict:
    return {
        "Authorization": f"Bearer {cfg.queue_api_token}",
        "Content-Type": "application/json",
    }


def claim(cfg: Config, items: list[ClaimItem]) -> list[ClaimResult]:
    """Claim a batch of items in one call. Empty input makes no request."""
    if not items:
        return []
    resp = requests.post(
        f"{cfg.queue_api_url}/claim",
        json={"items": items},
        headers=_headers(cfg),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["results"]


def complete(cfg: Config, items: list[CompleteItem]) -> None:
    """Report a batch of outcomes in one call. Empty input makes no request."""
    if not items:
        return
    resp = requests.post(
        f"{cfg.queue_api_url}/complete",
        json={"items": items},
        headers=_headers(cfg),
        timeout=15,
    )
    resp.raise_for_status()


def list_items(cfg: Config) -> list[dict]:
    """Return every item in the global queue (all machines, all time)."""
    resp = requests.get(f"{cfg.queue_api_url}/items", headers=_headers(cfg), timeout=15)
    resp.raise_for_status()
    return resp.json()["items"]
