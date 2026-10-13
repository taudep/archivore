#!/usr/bin/env bash
# Monday-morning automation: catch up archivore's own capture, ingest new
# articles into the personal wiki, generate and auto-publish this week's
# newsletter, then email a combined report of all three.
#
# Scheduled via launchd on this machine only — see docs/weekly-pipeline.md
# for the full design, why this has to be a single designated machine, and
# how to install/test it. Safe to run by hand any time:
#   ./scripts/weekly_pipeline.sh
#
# Deliberately not `set -e`: one step failing should never silence the
# report that would have told you it failed. Each step's exit code is
# captured and folded into the email instead of aborting the run.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAUDE_XYZ_DIR="$HOME/dev/taude.xyz"
CLAUDE_BIN="$HOME/.local/bin/claude"
UV_BIN="$(command -v uv || echo /opt/homebrew/bin/uv)"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_DIR="$HOME/Library/Logs/archivore/weekly-pipeline/$STAMP"
mkdir -p "$LOG_DIR"
PIPELINE_LOG="$LOG_DIR/pipeline.log"

log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$PIPELINE_LOG"; }

log "=== Weekly pipeline run: $STAMP ==="

if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY RUN — checking prerequisites only, no claude/archivore/publish calls"
  ok=1
  for bin in "$CLAUDE_BIN" "$UV_BIN"; do
    if [ ! -x "$bin" ] && ! command -v "$bin" >/dev/null 2>&1; then
      log "MISSING: $bin"
      ok=0
    fi
  done
  [ -d "$TAUDE_XYZ_DIR" ] || { log "MISSING: $TAUDE_XYZ_DIR"; ok=0; }
  [ -f "$REPO_DIR/scripts/send_weekly_email.py" ] || { log "MISSING: send_weekly_email.py"; ok=0; }
  cd "$REPO_DIR" && "$UV_BIN" run python3 -c "
from archivore.config import load_config
cfg = load_config()
print('smtp_host configured:', bool(cfg.smtp_host))
print('email_to configured:', bool(cfg.email_to))
" | tee -a "$PIPELINE_LOG"
  if [ "$ok" -eq 1 ]; then
    log "Dry run OK."
    exit 0
  else
    log "Dry run found missing prerequisites — see above."
    exit 1
  fi
fi

# --- 1. Archivore catch-up run ------------------------------------------
cd "$REPO_DIR"
"$UV_BIN" run archivore run > "$LOG_DIR/archivore_run.log" 2>&1
ARCHIVORE_EXIT=$?
log "archivore run: exit $ARCHIVORE_EXIT"

# --- 2. Wiki ingest (headless Claude, unattended) -----------------------
"$CLAUDE_BIN" -p "/taude ingest" --dangerously-skip-permissions \
  > "$LOG_DIR/taude_ingest.log" 2>&1
INGEST_EXIT=$?
log "taude ingest: exit $INGEST_EXIT"

# --- 3. Wiki status, for the email --------------------------------------
"$CLAUDE_BIN" -p "/taude status" --dangerously-skip-permissions \
  > "$LOG_DIR/taude_status.log" 2>&1
STATUS_EXIT=$?
log "taude status: exit $STATUS_EXIT"

# --- 4. Newsletter: scheduled automation run, auto-publishes ------------
cd "$TAUDE_XYZ_DIR"
NEWSLETTER_PROMPT="Use the last-week-in-review skill. This is the scheduled Monday automation run (no one is at the keyboard to review this) — per that skill's 'Automated (non-interactive) mode' section, skip the chat-approval step and auto-publish (set draft: false, commit, and push) once the build verifies clean, then still confirm the GitHub Actions deploy succeeded before finishing. End your final message with the newsletter's live URL alone on its own line, prefixed 'PUBLISHED: '."
"$CLAUDE_BIN" -p "$NEWSLETTER_PROMPT" --dangerously-skip-permissions \
  > "$LOG_DIR/newsletter.log" 2>&1
NEWSLETTER_EXIT=$?
log "newsletter: exit $NEWSLETTER_EXIT"

NEWSLETTER_URL="$(grep -oE 'PUBLISHED: https://taude\.xyz/newsletter/[A-Za-z0-9/_-]+' "$LOG_DIR/newsletter.log" | tail -1 | sed 's/^PUBLISHED: //')"
if [ -z "$NEWSLETTER_URL" ]; then
  # Fall back to any taude.xyz/newsletter/ URL mentioned anywhere in the log,
  # in case the final line didn't come through exactly as instructed.
  NEWSLETTER_URL="$(grep -oE 'https://taude\.xyz/newsletter/[A-Za-z0-9/_-]+' "$LOG_DIR/newsletter.log" | tail -1)"
fi

# --- 5. Compose and send the combined report email ----------------------
{
  echo "=== Archivore run (exit $ARCHIVORE_EXIT) ==="
  tail -n 20 "$LOG_DIR/archivore_run.log"
  echo
  echo "=== Wiki ingest (exit $INGEST_EXIT) ==="
  tail -n 30 "$LOG_DIR/taude_ingest.log"
  echo
  echo "=== Wiki status (exit $STATUS_EXIT) ==="
  cat "$LOG_DIR/taude_status.log"
  echo
  echo "=== Newsletter (exit $NEWSLETTER_EXIT) ==="
  if [ -n "$NEWSLETTER_URL" ]; then
    echo "Published: $NEWSLETTER_URL"
  else
    echo "No published URL found in the newsletter step's output — check the log below."
    tail -n 30 "$LOG_DIR/newsletter.log"
  fi
  echo
  echo "Full logs: $LOG_DIR"
} > "$LOG_DIR/email_body.txt"

cd "$REPO_DIR"
"$UV_BIN" run scripts/send_weekly_email.py \
  --subject "Weekly pipeline — $(date '+%b %d, %Y')" \
  --body-file "$LOG_DIR/email_body.txt" \
  >> "$PIPELINE_LOG" 2>&1
EMAIL_EXIT=$?
log "send_weekly_email: exit $EMAIL_EXIT"

log "Done. Logs in $LOG_DIR"
