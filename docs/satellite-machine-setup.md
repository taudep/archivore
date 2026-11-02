# Setting up a satellite machine (article sync only)

A guide for adding another machine that only captures — reads its own
browser history, fetches new articles, writes them into the shared vault —
and does nothing else. No wiki ingest, no newsletter, no email, none of
`post_run_cmd`. That's deliberate: only the one designated machine runs
[the weekly pipeline](weekly-pipeline.md); every other machine's job is
narrower and safer by construction.

If you're setting up the designated machine itself, see
[weekly-pipeline.md](weekly-pipeline.md) instead — this doc is for every
*other* machine.

## What a satellite machine needs, and what it doesn't

| Needs | Doesn't need |
|---|---|
| This repo (`archivore` itself) | `~/dev/taude.xyz`, `~/dev/agentfiles` |
| Access to the same synced vault folder | Claude Code, the `taude`/`last-week-in-review` skills |
| The shared queue's URL + token | SMTP/email config (optional, see below) |
| A cron/scheduled job that finishes by Sunday night | `post_run_cmd` set to anything |

Each machine scans its *own* local browser history — that's the whole point
of having more than one. The shared queue (`archivore-queue`) is what keeps
the same article from being fetched twice across machines; it has nothing to
do with which machine reads which browser.

## 1. Prerequisite: the shared vault must already be syncing here

This is the step most likely to go wrong silently, so verify it before
anything else. All machines write articles into the same folder — by
default, `Todd's Obsidian Vault/Archivore/Raw/` inside iCloud Drive
(`~/Library/Mobile Documents/com~apple~CloudDocs/` on macOS). Before
installing archivore on the new machine:

1. Sign this machine into the same Apple ID/iCloud account the other
   machines use, with iCloud Drive enabled (System Settings → Apple ID →
   iCloud → iCloud Drive). On Windows, install the
   [iCloud for Windows](https://support.apple.com/en-us/104845) app instead.
2. Wait for the initial sync to finish, then confirm the folder is actually
   there and already has content — don't take "the app says it's signed in"
   as proof:
   ```sh
   ls "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw" | wc -l
   ```
   This should already show the full count of existing articles (hundreds),
   not zero or a handful — iCloud syncs the existing backlog before this
   machine ever runs archivore. If it's empty or still climbing slowly,
   wait and re-check rather than proceeding; a premature `archivore run`
   here won't corrupt anything, but its first run will look like every
   article in the vault is new and waste time re-indexing what's already
   there.
3. If this machine can't run iCloud Drive at all (no first-party client —
   this is the situation on Linux), you'll need a different shared-folder
   mechanism (Dropbox, Syncthing, a git repo, etc.) that all machines agree
   on, and `output_dir` pointed at wherever that lands locally. That's a
   bigger decision than this doc covers — ask if you want to go that route.

## 2. Install archivore

```sh
git clone https://github.com/taudep/archivore.git
cd archivore
uv sync --extra firefox    # firefox extra adds lz4 session decoding
uv tool install .          # puts `archivore` on PATH
```

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/); install `uv`
first if this machine doesn't have it (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

## 3. Configure — satellite-machine config

Create `~/.config/archivore/config.yaml`:

```yaml
# Same shared queue every machine points at — coordinates dedup across all
# of them. Get the token from the Worker's stored secret, or copy it from
# another machine's config yourself; don't ask Claude for its value in chat.
queue_api_url: https://archivore-queue.taude.workers.dev
queue_api_token: "your token here"

# Must match the folder from step 1 exactly, including on this machine.
output_dir: ~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw

# Leave enable_reddit unset/false and post_run_cmd unset — this machine
# only captures, it doesn't ingest or chain anything.
```

That's the whole satellite config. Specifically **don't** set:
- `post_run_cmd` — reserved for the one designated machine (see
  [weekly-pipeline.md](weekly-pipeline.md#why-this-has-to-be-one-designated-machine))
- `smtp_host`/`email_to` — optional per-machine run notifications are fine
  if you want them (see the main [README's Automation section](../README.md#automation-cron)),
  but the combined weekly report only comes from the designated machine

`chmod 600 ~/.config/archivore/config.yaml` once the token's in there — same
reasoning as the SMTP password: it's a credential sitting in plaintext.

## 4. First run — by hand, before scheduling anything

```sh
archivore run
```

Watch the output. On a brand-new satellite machine the first run should
show a small number of discovered items (whatever's in this machine's own
browser history since... well, since always, the first time) — not hundreds,
and not zero unless this machine's browser history is genuinely empty of
HN/X links. Check the printed config block at the top confirms the right
`queue_api_url` and `output_dir` before trusting the rest of the run.

Then confirm dedup is actually working: if you've visited an HN link on
*this* machine that another machine already fetched, `archivore run` should
report it as already-claimed/skipped, not re-fetch and re-save it. If you
see a duplicate file show up in the vault for an article you know another
machine already has, something's wrong with the shared `queue_api_token` or
`queue_api_url` — check both match exactly across machines before anything
else.

## 5. Schedule it — cron, finishing by Sunday night

The designated machine's Monday-morning pipeline (ingest + newsletter)
assumes every satellite machine's captures for the week have already synced
into the shared vault by the time it runs. Give it a comfortable buffer —
cron the capture daily, or at least by Sunday evening:

```cron
# Every day at 8pm
0 20 * * * cd /path/to/archivore && /path/to/uv run archivore run >> ~/Library/Logs/archivore/cron.log 2>&1
```

(`archivore run` is cheap to run more often than this — it only processes
what's new since its last successful run — so daily is a safe default
even on a machine you don't check often.)

If this machine's cron can't reliably land before Sunday night (unreliable
power/network, a laptop that's often asleep), it's not a hard failure —
see [weekly-pipeline.md](weekly-pipeline.md#what-it-assumes-about-the-other-machines)
for what happens instead (that week's captures just show up in the
*following* Monday's newsletter).

## Checklist

- [ ] Vault folder confirmed present and fully synced (step 1.2) *before*
      installing archivore
- [ ] `archivore` installed and on PATH (`uv tool install .`)
- [ ] `~/.config/archivore/config.yaml` has `queue_api_url`, `queue_api_token`,
      and `output_dir` — and nothing else (no `post_run_cmd`)
- [ ] Config file permissions restricted (`chmod 600`)
- [ ] First manual `archivore run` looks sane: right config printed, no
      unexpected duplicate fetches
- [ ] Cron/scheduled job installed, timed to finish before Sunday night
