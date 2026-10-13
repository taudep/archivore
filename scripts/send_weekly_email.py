# /// script
# requires-python = ">=3.12"
# ///
"""Send the combined weekly-pipeline report email.

Reuses archivore's own SMTP config (~/.config/archivore/config.yaml,
smtp_host/smtp_port/smtp_user/smtp_password/email_to/email_from) so
credentials live in exactly one place. Called by scripts/weekly_pipeline.sh
after the archivore run, wiki ingest, and newsletter steps all finish.

Never raises on a sending failure — a broken email step should never look
like a crash to whatever's watching the pipeline's own exit code; it prints
the error to stderr and returns non-zero so the caller can log it instead.
"""

import argparse
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archivore.config import load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    args = parser.parse_args()

    cfg = load_config()
    if not (cfg.smtp_host and cfg.email_to):
        print(
            "send_weekly_email: smtp_host/email_to not configured in "
            "~/.config/archivore/config.yaml — skipping",
            file=sys.stderr,
        )
        return 1

    body = args.body_file.read_text(encoding="utf-8")
    msg = EmailMessage()
    msg["Subject"] = args.subject
    msg["From"] = cfg.email_from or cfg.smtp_user
    msg["To"] = cfg.email_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if cfg.smtp_user and cfg.smtp_password:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        print(f"send_weekly_email: failed to send: {e}", file=sys.stderr)
        return 1

    print(f"send_weekly_email: sent to {cfg.email_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
