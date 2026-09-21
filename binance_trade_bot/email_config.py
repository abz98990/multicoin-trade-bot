"""
Email digest configuration reader.

Reads from email.cfg (an INI file with an [email] section) and / or
environment variables.  email.cfg is kept completely separate from user.cfg
so SMTP credentials never share a file with Binance API keys.

Priority (highest to lowest):
  1. Environment variable  (e.g. EMAIL_PASSWORD in a Docker secret)
  2. email.cfg value       (the normal way to configure on a server)
  3. Built-in default      (safe no-op: enabled=no, everything else empty)

email.cfg is optional.  If it is missing the module stays disabled and no
exception is raised — the bot runs normally without sending any email.
"""
import configparser
import os

# ── File and section names ────────────────────────────────────────────────────
_CFG_FILE = "email.cfg"
_CFG_SECTION = "email"

# ── Defaults (all safe / disabled) ───────────────────────────────────────────
_DEFAULTS = {
    "enabled":        "no",
    "host":           "smtp.gmail.com",
    "port":           "587",
    "username":       "",
    "password":       "",
    "recipient":      "",
    "interval_hours": "1",
}

# ── Env-var names that override email.cfg ────────────────────────────────────
_ENV = {
    "enabled":        "EMAIL_ENABLED",
    "host":           "EMAIL_HOST",
    "port":           "EMAIL_PORT",
    "username":       "EMAIL_USERNAME",
    "password":       "EMAIL_PASSWORD",
    "recipient":      "EMAIL_RECIPIENT",
    "interval_hours": "EMAIL_INTERVAL_HOURS",
}


class EmailConfig:
    """
    Email digest settings, loaded from email.cfg and / or environment variables.

    Attributes
    ----------
    ENABLED         : bool   – master on/off switch (email_enabled in [email])
    HOST            : str    – SMTP server hostname
    PORT            : int    – 587 = STARTTLS (default), 465 = SMTP_SSL
    USERNAME        : str    – login / From address
    PASSWORD        : str    – SMTP password or App Password
    RECIPIENT       : str    – delivery address for the digest
    INTERVAL_HOURS  : float  – default send interval; DB value takes priority

    Re-instantiate to pick up file changes without restarting the bot.
    """

    CFG_FILE = _CFG_FILE  # exposed so tests can override

    def __init__(self):
        cfg = configparser.ConfigParser(default_section="DEFAULT")
        # Seed with safe defaults so a partial email.cfg still works.
        cfg[_CFG_SECTION] = dict(_DEFAULTS)

        cfg_path = self.CFG_FILE
        if os.path.exists(cfg_path):
            cfg.read(cfg_path, encoding="utf-8")

        def _get(key: str) -> str:
            """Env var wins; fall back to email.cfg / default."""
            return os.environ.get(_ENV[key]) or cfg.get(_CFG_SECTION, key, fallback=_DEFAULTS[key])

        self.ENABLED        = _get("enabled").lower() in ("yes", "true", "1")
        self.HOST           = _get("host")
        self.PORT           = int(_get("port") or 587)
        self.USERNAME       = _get("username")
        self.PASSWORD       = _get("password")
        self.RECIPIENT      = _get("recipient")
        self.INTERVAL_HOURS = float(_get("interval_hours") or 1)

    # ─────────────────────────────────────────────────────────────────── helpers

    def is_fully_configured(self) -> bool:
        """True when all four required SMTP fields are non-empty."""
        return bool(self.HOST and self.USERNAME and self.PASSWORD and self.RECIPIENT)

    def info(self) -> dict:
        """Public-safe summary — password is intentionally omitted."""
        return {
            "enabled":        self.ENABLED,
            "host":           self.HOST,
            "port":           self.PORT,
            "username":       self.USERNAME,
            "recipient":      self.RECIPIENT,
            "interval_hours": self.INTERVAL_HOURS,
            "cfg_file":       self.CFG_FILE,
            "cfg_exists":     os.path.exists(self.CFG_FILE),
        }
