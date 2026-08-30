# Queue Migration & Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the real, existing local `hn_this_week/queue.db` history and `.md` files into the new centralized system, then retire the old local files for good.

**Architecture:** A one-off script bulk-imports every row from the old local queue into D1 via the same `/claim` + `/complete` API the running system already uses, then the Markdown files move by hand into the new `output_dir`, and the old files are deleted.

**Tech Stack:** Python 3.12, `requests` (already a dependency).

## Global Constraints

- **Do not start this plan until you trust the new system.** This is the one plan in the whole design that touches real, irreplaceable data — your actual reading history and the Markdown you've already fetched. Everything up to this point (the Worker plan, the client-integration plan) is either new infrastructure or code changes covered by tests and git history; nothing in those plans can lose your data. This plan can, if run against a system you haven't actually verified works.
- **Concrete readiness bar before starting:** you've run `docs/superpowers/plans/2026-08-30-queue-client-integration.md`'s Task 6 (end-to-end verification) successfully, *and* you've used `archivore run` for real, unassisted, at least a few times across at least two machines, and seen dedup actually behave correctly (an item fetched on one machine shows up — via iCloud — without being re-fetched on the other). If you haven't done that yet, stop and do that first.
- This plan is a one-time cutover, not a repeatable operation. There's no "undo" task — the safety net is: don't run Step 4 (delete the old files) until Steps 1–3 are confirmed correct.

---

## File Structure

```
scripts/
  migrate_queue_to_d1.py   - NEW, one-off, not part of the shipped CLI
```

---

### Task 1: Migrate and cut over

**Files:**
- Create: `scripts/migrate_queue_to_d1.py`

This is a one-off operational script, not part of the shipped CLI — it's run once by hand during cutover, not tested with `pytest` (matches the spec's Migration section: "a one-off script, not part of the shipped CLI").

- [ ] **Step 1: Back up the old data before touching anything**

```bash
cp hn_this_week/queue.db /tmp/queue.db.backup-$(date +%Y%m%d)
cp -r hn_this_week /tmp/hn_this_week.backup-$(date +%Y%m%d)
```

If anything below goes wrong, these backups are the recovery path — nothing later in this task modifies `/tmp`.

- [ ] **Step 2: Create the scripts directory and the migration script**

```bash
mkdir -p scripts
```

`scripts/migrate_queue_to_d1.py`:

```python
#!/usr/bin/env python3
"""One-off: bulk-import an existing local hn_this_week/queue.db into the
archivore-queue D1 database via the coordination API, then report which
.md files still need to be moved into the new output_dir.

Usage:
    QUEUE_API_URL=https://archivore-queue.<sub>.workers.dev \\
    QUEUE_API_TOKEN=<token> \\
    python3 scripts/migrate_queue_to_d1.py hn_this_week/queue.db
"""

import os
import sqlite3
import sys

import requests


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: migrate_queue_to_d1.py <path-to-old-queue.db>")

    db_path = sys.argv[1]
    api_url = os.environ["QUEUE_API_URL"].rstrip("/")
    token = os.environ["QUEUE_API_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM queue").fetchall()
    conn.close()

    print(f"Found {len(rows)} row(s) in {db_path}")

    claim_items = [
        {
            "item_id": r["item_id"],
            "source": r["source"],
            "comments_url": r["comments_url"],
            "article_url": r["article_url"],
        }
        for r in rows
    ]
    resp = requests.post(f"{api_url}/claim", json={"items": claim_items}, headers=headers, timeout=30)
    resp.raise_for_status()
    print(f"Claimed {len(claim_items)} item(s) in D1")

    complete_items = [
        {
            "item_id": r["item_id"],
            "status": r["status"],
            "title": r["title"],
            "is_selfpost": bool(r["is_selfpost"]),
            "filename": r["filename"],
            "last_error": r["last_error"],
        }
        for r in rows
    ]
    resp = requests.post(
        f"{api_url}/complete", json={"items": complete_items}, headers=headers, timeout=30
    )
    resp.raise_for_status()
    print(f"Reported status/title/filename for {len(complete_items)} item(s)")

    filenames = [r["filename"] for r in rows if r["filename"]]
    print(f"\n{len(filenames)} .md file(s) still need to move by hand into the new output_dir:")
    print("  mv hn_this_week/*.md \"<your RAW folder path>/\"")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it against the real deployed Worker**

```bash
QUEUE_API_URL=https://archivore-queue.<your-subdomain>.workers.dev \
QUEUE_API_TOKEN=<your-real-token> \
python3 scripts/migrate_queue_to_d1.py hn_this_week/queue.db
```

Expected: prints the row count claimed/reported, then the `mv` reminder.

- [ ] **Step 4: Verify the import before moving or deleting anything**

```bash
cd worker && wrangler d1 execute archivore-queue --remote --command \
  "SELECT COUNT(*) AS n FROM queue"
```

Expected: `n` matches (or exceeds, if you've already run `archivore run` for real against the new system before this migration) the row count printed in Step 3. If it doesn't match, stop — do not proceed to Step 5 — and investigate before touching the local files.

- [ ] **Step 5: Move the existing Markdown files by hand**

```bash
mkdir -p "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw"
mv hn_this_week/*.md "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw/"
```

- [ ] **Step 6: Confirm the files landed correctly**

```bash
ls "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw/" | wc -l
```

Expected: roughly matches the old `hn_this_week/*.md` count (`index.md` plus one file per completed item).

- [ ] **Step 7: Only now, retire the old local queue database and directory**

```bash
rm hn_this_week/queue.db
rmdir hn_this_week 2>/dev/null || echo "hn_this_week/ not empty — check for leftover files before removing"
```

- [ ] **Step 8: Commit the script**

```bash
git add scripts/migrate_queue_to_d1.py
git commit -m "Add one-off queue.db -> D1 migration script; complete real-data cutover"
```

- [ ] **Step 9: Clean up the temp backups once you're confident, not before**

```bash
rm -rf /tmp/queue.db.backup-* /tmp/hn_this_week.backup-*
```

This step is intentionally last and separate — leave the backups in place for a few days of real use before deleting them.

---

## Self-Review Notes

- **Spec coverage:** the spec's Migration section (bulk-import queue.db rows, move `.md` files, retire the old queue.db) is fully covered by this plan's single task.
- **Deliberate deviation from a "plan" in the usual sense:** every other plan in this project ends each task with an automated test. This one can't — it operates on real, already-existing production data, not code under test. In its place: a backup step (Step 1) before any mutation, and a verification step (Step 4) gating the point of no return (Step 7), which is the closest equivalent to "test before you trust it" available for a data-migration task.
- **Placeholder scan:** every step has complete, runnable code — no TBD/TODO.
- **Why this is a separate plan from `2026-08-30-queue-client-integration.md`:** that plan's tasks are all reversible via git (delete a source file, revert a commit) and don't touch the user's actual queue/article history. This plan deletes real local files. Bundling them risked running the irreversible step the moment the code merged, before the code had actually been trusted through real use — see this plan's Global Constraints.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-30-queue-migration-cutover.md`.**
