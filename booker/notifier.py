"""Email summary notifier."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Config

log = logging.getLogger(__name__)


def send_summary(cfg: Config, subject: str, body: str) -> bool:
    if not cfg.smtp_user or not cfg.smtp_app_password or not cfg.notify_to:
        log.warning("SMTP not fully configured — skipping email. Body was:\n%s", body)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.notify_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
            s.starttls()
            s.login(cfg.smtp_user, cfg.smtp_app_password)
            s.send_message(msg)
        log.info("notification email sent to %s", cfg.notify_to)
        return True
    except Exception as e:
        log.exception("smtp send failed: %s", e)
        return False
