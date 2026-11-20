"""SMTP access. In M1 only the login check used by the config flow; sending follows in M6."""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field

from ..const import SMTP_TIMEOUT_S, Security
from .errors import GmxConnectionError, GmxSmtpAuthError, GmxTlsError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """SMTP connection parameters. The password is excluded from ``repr``."""

    host: str
    port: int
    security: Security
    username: str
    password: str = field(repr=False)


def _open(settings: SmtpSettings, context: ssl.SSLContext) -> smtplib.SMTP:
    """Connect and secure the connection; never continues without TLS (blocking)."""
    if settings.security is Security.SSL:
        return smtplib.SMTP_SSL(settings.host, settings.port, timeout=SMTP_TIMEOUT_S, context=context)
    smtp = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_S)
    try:
        smtp.ehlo()
        if not smtp.has_extn("starttls"):
            raise GmxTlsError("STARTTLS not offered by the SMTP server")
        smtp.starttls(context=context)
        smtp.ehlo()
    except BaseException:
        smtp.close()
        raise
    return smtp


def check_smtp_login(settings: SmtpSettings, context: ssl.SSLContext) -> int | None:
    """Log in and log out again; return the announced ``SIZE`` limit in bytes, if any (blocking)."""
    smtp: smtplib.SMTP | None = None
    try:
        smtp = _open(settings, context)
        smtp.login(settings.username, settings.password)
        size = smtp.esmtp_features.get("size", "").strip()
        return int(size) if size.isdigit() else None
    except smtplib.SMTPAuthenticationError as err:
        raise GmxSmtpAuthError("SMTP login rejected") from err
    except ssl.SSLError as err:
        raise GmxTlsError(f"SMTP TLS failed: {type(err).__name__}") from err
    except (smtplib.SMTPException, OSError) as err:
        raise GmxConnectionError(f"SMTP connection failed: {type(err).__name__}") from err
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except smtplib.SMTPException, OSError:
                smtp.close()
