# Weekly pipeline: the Monday-morning automation

Every Monday morning, one designated machine runs the whole personal-workflow
loop end to end, unattended, and emails a report. This document is the full
design: what runs, in what order, why it has to be exactly one machine, what
it assumes about the other machines, and how to install, test, and debug it.

## What it does, in order

1. **`archivore run`** — this machine's own catch-up capture (its own browser
   tabs/history since the last run). Multi-machine dedup happens through the
   shared `archivore-queue` Worker, so this never re-fetches an item another
   machine already claimed.
2. **`/taude ingest`** (headless, via `claude -p`) — files every new article
   in the shared vault (from this machine *and* every other machine that has
   already synced via iCloud) into the personal wiki's topic pages.
3. **`/taude status`** (headless) — captured for the email, not acted on.
4. **`/last-week-in-review`** (headless) — generates this week's newsletter
   from the vault and **auto-publishes it** (commits, pushes, confirms the
   GitHub Actions deploy). This is the one deliberate exception to the "ask
   before publishing" rule the rest of this project follows — see
   **Why auto-publish** below.
5. **One combined email** — archivore's run summary, the wiki status, and
   the newsletter's live URL, all in a single message.

Implemented as `scripts/weekly_pipeline.sh`, orchestrated by a launchd job
(`scripts/com.taude.archivore-weekly.plist`).

## Why this has to be one designated machine

Archivore runs on multiple machines, each scanning its own local browser
history — a different, machine-specific set of candidate URLs every time.
All of them write into the *same* iCloud-synced Obsidian vault, and the
shared queue (`archivore-queue`) prevents the same item being fetched twice
across machines. But the wiki-ingest and newsletter steps aren't per-machine
— they read the whole shared vault and produce one wiki, one newsletter.
Running steps 2–5 on more than one machine would ingest the same articles
twice, publish the newsletter twice, and send the report email twice. Only
step 1 is safe to run everywhere; steps 2–5 must run on exactly one machine.

This machine is that machine.

## What it assumes about the other machines

Your other machines run their own `archivore run` on their own cron (not
this pipeline — just the capture step), **completing by Sunday night**, so
their captures have time to sync via iCloud before this job runs Monday
morning. If a machine's cron runs late, or iCloud sync lags, that machine's
captures from the past week will simply show up in *next* Monday's
newsletter instead — not lost, just a week later. If that's a tighter
constraint than any machine can reliably meet, push the launchd job's
`Hour` later in the day rather than fighting the sync lag.

## Why auto-publish

Every other post on this blog — and every earlier run of this very skill —
gets shown to you in chat before it goes live. This pipeline is the one
exception, and it's narrow and explicit by design: `weekly_pipeline.sh`'s
prompt to Claude states plainly that this is the scheduled automation run
with no one at the keyboard, and the `last-week-in-review` skill's
"Automated (non-interactive) mode" section only skips its review gate when
told that explicitly. Typing `/last-week-in-review` yourself, or asking for
"this week's newsletter" in chat, always keeps the normal review step — this
exception doesn't change that.

The trade-off: a bad week (a mis-shortlisted item, a blurb that misses the
actual point) ships without your eyes on it first. The report email exists
specifically so you find out within minutes, not whenever you happen to
check the blog — if something's off, the fix is the same as any other post:
edit the vault file and re-run `publish.sh`.

## Prerequisites

1. **The script must be executable:**
   ```sh
   chmod +x ~/dev/python-test/scripts/weekly_pipeline.sh
   ```
2. **SMTP credentials**, in `~/.config/archivore/config.yaml` (same fields
   the "Automation (cron)" section of the main README already documents —
   not duplicated here since that's the single source of truth):
   ```yaml
   smtp_host: smtp.gmail.com
   smtp_port: 587
   smtp_user: you@gmail.com
   smtp_password: "an app password, not your account password"
   email_to: you@gmail.com
   ```
   `chmod 600 ~/.config/archivore/config.yaml` afterward, since
   `smtp_password` lives there in plaintext. Add these yourself — this is a
   credential, not something to paste into chat.
3. **`claude` and `uv` resolvable by absolute path** — the script already
   hardcodes `~/.local/bin/claude` and searches `PATH` then falls back to
   `/opt/homebrew/bin/uv`; adjust the top of `weekly_pipeline.sh` if either
   binary lives somewhere else on this machine.

## Installing the schedule

```sh
cp ~/dev/python-test/scripts/com.taude.archivore-weekly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.taude.archivore-weekly.plist
```

Default fire time is Monday 7:00am — edit the `Hour`/`Minute` keys in the
plist before loading if you want a different time (see **What it assumes
about the other machines** above for why later might be worth it).

To stop it: `launchctl unload ~/Library/LaunchAgents/com.taude.archivore-weekly.plist`.

## Testing before you trust it

```sh
# Checks binaries resolve and SMTP/email config is present — makes no
# archivore/claude/publish calls, sends no email.
~/dev/python-test/scripts/weekly_pipeline.sh --dry-run

# The real thing, on demand, without waiting for Monday:
~/dev/python-test/scripts/weekly_pipeline.sh
```

Run the real thing by hand at least once before relying on the schedule —
the first real run is also the first real test of the `--dangerously-skip-permissions`
flag against your actual `claude` setup (see **A note on `--dangerously-skip-permissions`**
below), and you want to see that fail loudly in your own terminal, not
silently at 7am.

## Logs

Every run writes to its own timestamped directory:
```
~/Library/Logs/archivore/weekly-pipeline/<YYYYMMDD-HHMMSS>/
  pipeline.log        # step-by-step progress + exit codes
  archivore_run.log    taude_ingest.log    taude_status.log    newsletter.log
  email_body.txt       # exactly what got emailed
```
launchd's own stdout/stderr (startup failures, before the script's own
logging kicks in) land in `weekly-pipeline/launchd.{out,err}.log`.

## A note on `--dangerously-skip-permissions`

The `/taude ingest` and newsletter steps run via `claude -p ... --dangerously-skip-permissions`
because there's no one at the keyboard to approve tool calls at 7am on a
Monday. That flag is real: it bypasses every one of Claude Code's permission
prompts for that invocation. The mitigations here are that it only runs on
this one machine, only against these two specific, scoped repos, every run
is fully logged, and every run emails you a report — so a bad call surfaces
within minutes rather than silently. If you later tighten this, the two
places to look are `scripts/weekly_pipeline.sh`'s `claude -p` invocations and
the flag itself.

## Component map

| Piece | Lives at |
|---|---|
| Orchestration script | `scripts/weekly_pipeline.sh` (this repo) |
| Email sender | `scripts/send_weekly_email.py` (this repo) |
| launchd job | `scripts/com.taude.archivore-weekly.plist` (this repo) |
| Wiki ingest/status | `taude` skill, `~/dev/agentfiles/skills/taude/SKILL.md` |
| Newsletter generation | `last-week-in-review` skill, `~/dev/taude.xyz/skills/last-week-in-review/SKILL.md` — see its "Automated (non-interactive) mode" section |
| SMTP config | `~/.config/archivore/config.yaml` (shared with archivore's own per-run email, see main README) |
