"""
Hourly/6-hourly email summary for the trading bot.

Sends a rich-HTML digest email with:
  - Portfolio value vs holding and BTC benchmarks
  - Current position (coin, entry, stop, target, unrealised P&L)
  - Activity log events since the last email (jumps, risk hits, etc.)
  - Parked-in-bridge notice if applicable

The send interval is live-editable from the dashboard without a restart.
The module checks once per minute whether enough time has elapsed, which
means an interval change takes effect on the next minute tick at most.

SMTP setup is read from user.cfg / environment variables on every send,
so you can rotate credentials without restarting the bot.
"""
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from traceback import format_exc
from typing import Optional

# ---------------------------------------------------------------------------
# Colour palette (matches the dashboard dark theme)
# ---------------------------------------------------------------------------
_C = {
    "bg": "#0f1117",
    "card": "#1a1d27",
    "border": "#2a2d3e",
    "accent": "#6c63ff",
    "green": "#22c55e",
    "red": "#ef4444",
    "amber": "#f59e0b",
    "text": "#e2e8f0",
    "muted": "#64748b",
}

# ---------------------------------------------------------------------------
# HTML template helpers
# ---------------------------------------------------------------------------


def _pct_color(value: Optional[float]) -> str:
    if value is None:
        return _C["muted"]
    return _C["green"] if value >= 0 else _C["red"]


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.3f}%"


def _fmt_price(value: Optional[float], decimals: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def _table_row(label: str, value: str, color: str = "") -> str:
    style = f"color:{color};" if color else ""
    return (
        f'<tr><td style="padding:6px 12px;color:{_C["muted"]};white-space:nowrap">{label}</td>'
        f'<td style="padding:6px 12px;text-align:right;font-weight:600;{style}">{value}</td></tr>'
    )


def _section(title: str, rows_html: str) -> str:
    return f"""
    <div style="margin-bottom:24px;background:{_C["card"]};border:1px solid {_C["border"]};
                border-radius:10px;overflow:hidden">
      <div style="padding:10px 14px;background:{_C["border"]};font-size:11px;
                  letter-spacing:0.08em;text-transform:uppercase;color:{_C["muted"]}">
        {title}
      </div>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;color:{_C["text"]};font-size:14px">
        {rows_html}
      </table>
    </div>"""


def _activity_row(entry: dict) -> str:
    cat = entry.get("category", "")
    msg = entry.get("message", "")
    ts_raw = entry.get("datetime", "")
    try:
        ts = datetime.fromisoformat(ts_raw).strftime("%Y-%m-%d %H:%M")
    except Exception:  # pylint: disable=broad-except
        ts = ts_raw[:16]

    cat_color = {
        "jump": _C["accent"],
        "risk": _C["amber"],
        "trade": _C["green"],
    }.get(cat, _C["muted"])

    return (
        f'<tr style="border-top:1px solid {_C["border"]}">'
        f'<td style="padding:6px 12px;width:1%;white-space:nowrap;color:{_C["muted"]};font-size:12px">{ts}</td>'
        f'<td style="padding:6px 12px;width:1%;white-space:nowrap">'
        f'<span style="background:{cat_color}22;color:{cat_color};padding:2px 8px;border-radius:4px;'
        f'font-size:11px;text-transform:uppercase">{cat}</span></td>'
        f'<td style="padding:6px 12px;color:{_C["text"]}">{msg}</td>'
        f"</tr>"
    )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class EmailNotifier:
    """
    Checks once per minute whether a digest should be sent, and fires if
    enough time has elapsed since the last successful send.

    Parameters
    ----------
    db          : Database      – shared DB handle
    config      : Config        – trading config (BRIDGE, TESTNET, etc.)
    email_cfg   : EmailConfig   – email-specific settings (SMTP, interval)
    logger      : Logger

    The send interval is always read live from the DB so dashboard changes
    take effect on the next minute tick without a restart. On first run the
    DB is seeded with email_cfg.INTERVAL_HOURS so the user.cfg value is
    honoured without a manual dashboard visit.
    """

    def __init__(self, db, config, email_cfg, logger):
        self.db = db
        self.config = config          # trading config: BRIDGE.symbol, TESTNET, …
        self.email_cfg = email_cfg    # email config: ENABLED, HOST, PORT, …
        self.logger = logger
        self._last_sent: float = 0.0

        # Seed the DB with the user.cfg interval if no DB row exists yet,
        # so the configured value is effective from the very first check.
        self._seed_interval()

    # ------------------------------------------------------------------ public

    def check_and_send(self):
        """Called every minute by the scheduler."""
        if not self.email_cfg.ENABLED:
            return
        interval_hours = self.db.get_email_interval_hours()
        if interval_hours <= 0:
            return
        elapsed = time.time() - self._last_sent
        if elapsed >= interval_hours * 3600:
            self._send()

    def send_now(self) -> str:
        """Force an immediate send and return '' on success or an error string."""
        if not self.email_cfg.ENABLED:
            return "Email not enabled. Set email_enabled=yes in user.cfg."
        return self._send()

    # ----------------------------------------------------------------- private

    def _seed_interval(self):
        """Write user.cfg interval to DB if no explicit DB value has been set yet."""
        try:
            from .models.scout_settings import ScoutSettings  # local import
            with self.db.db_session() as session:
                row = session.query(ScoutSettings).get(1)
                if row is None or row.email_interval_hours is None:
                    self.db.set_email_interval_hours(self.email_cfg.INTERVAL_HOURS)
        except Exception:  # pylint: disable=broad-except
            pass  # non-fatal: DB might not be initialised yet

    # ----------------------------------------------------------------- private

    def _send(self) -> str:
        """Build and dispatch the email. Returns '' on success, error string otherwise."""
        try:
            html = self._build_html()
            subject = self._build_subject()
            error = self._smtp_send(subject, html)
            if error:
                self.logger.warning(f"Email digest failed: {error}")
                return error
            self.logger.info("Email digest sent")
            self._last_sent = time.time()
            return ""
        except Exception:  # pylint: disable=broad-except
            tb = format_exc()
            self.logger.warning(f"Email digest raised an exception:\n{tb}")
            return tb.splitlines()[-1]

    def _build_subject(self) -> str:
        coin = self.db.get_current_coin()
        symbol = coin.symbol if coin else "—"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"Trade Bot Update — {symbol} — {now}"

    def _build_html(self) -> str:
        bridge = self.config.BRIDGE.symbol
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        interval = self.db.get_email_interval_hours()

        # ── portfolio / benchmarks ──────────────────────────────────────────
        perf_rows = self._build_performance_rows(bridge)

        # ── position ────────────────────────────────────────────────────────
        position_rows = self._build_position_rows(bridge)

        # ── parked notice ────────────────────────────────────────────────────
        parked_html = ""
        if self.db.get_parked_in_bridge():
            parked_html = f"""
            <div style="margin-bottom:24px;padding:12px 16px;background:#f59e0b22;
                        border:1px solid {_C["amber"]};border-radius:10px;
                        color:{_C["amber"]};font-size:13px">
              ⚠️ <strong>Parked in {bridge}</strong> — Take-profit triggered.
              Bot is holding {bridge} and waiting for the next ratio signal before re-entering.
              <a href="http://localhost:5123" style="color:{_C["amber"]}">Clear from dashboard →</a>
            </div>"""

        # ── activity ────────────────────────────────────────────────────────
        activity_html = self._build_activity_html()

        # ── footer ───────────────────────────────────────────────────────────
        testnet_badge = (
            f'<span style="background:{_C["amber"]}22;color:{_C["amber"]};'
            f'padding:2px 8px;border-radius:4px;font-size:11px">TESTNET</span>'
            if self.config.TESTNET
            else f'<span style="background:{_C["red"]}22;color:{_C["red"]};'
            f'padding:2px 8px;border-radius:4px;font-size:11px">LIVE</span>'
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trade Bot Update</title></head>
<body style="margin:0;padding:0;background:{_C["bg"]};font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:{_C["text"]}">
  <div style="max-width:640px;margin:32px auto;padding:0 16px">

    <!-- header -->
    <div style="margin-bottom:24px;text-align:center">
      <div style="font-size:20px;font-weight:700;letter-spacing:-0.02em;margin-bottom:6px">
        📊 Trade Bot Update {testnet_badge}
      </div>
      <div style="color:{_C["muted"]};font-size:12px">{now_str} · next in {interval}h</div>
    </div>

    {parked_html}
    {perf_rows}
    {position_rows}
    {activity_html}

    <!-- footer -->
    <div style="text-align:center;color:{_C["muted"]};font-size:11px;margin-top:8px;padding-bottom:32px">
      Interval: {interval}h ·
      <a href="http://localhost:5123" style="color:{_C["muted"]}">Open dashboard</a>
    </div>
  </div>
</body>
</html>"""

    # ----------------------------------------------------------------- sections

    def _build_performance_rows(self, bridge: str) -> str:
        """Portfolio value and benchmark comparison."""
        from .models import EquitySnapshot  # local import to avoid circular

        rows = ""
        try:
            with self.db.db_session() as session:
                snap: EquitySnapshot = (
                    session.query(EquitySnapshot)
                    .order_by(EquitySnapshot.datetime.desc())
                    .first()
                )
                if snap is None:
                    return _section(
                        "Portfolio",
                        _table_row("Status", "No equity data yet — bot may still be initialising"),
                    )
                info = snap.info()
        except Exception:  # pylint: disable=broad-except
            return ""

        total = info.get("total_usdt")
        hodl = info.get("bench_hodl")
        btc = info.get("bench_btc")

        vs_hodl = ((total / hodl - 1) * 100) if total and hodl else None
        vs_btc = ((total / btc - 1) * 100) if total and btc else None

        rows += _table_row(f"Portfolio ({bridge})", f"{_fmt_price(total, 2)} {bridge}")
        rows += _table_row("vs Holding", _fmt_pct(vs_hodl), _pct_color(vs_hodl))
        rows += _table_row("vs BTC", _fmt_pct(vs_btc), _pct_color(vs_btc))
        if info.get("n_unpriced"):
            rows += _table_row("Unpriced assets", str(info["n_unpriced"]), _C["amber"])

        return _section("Portfolio", rows)

    def _build_position_rows(self, bridge: str) -> str:
        """Current held coin with risk levels."""
        try:
            position = self.db.get_current_position()
            coin = self.db.get_current_coin()
        except Exception:  # pylint: disable=broad-except
            return ""

        if coin is None:
            return _section("Position", _table_row("Status", "No current coin"))

        rows = _table_row("Coin", coin.symbol)

        if position:
            entry = position.get("entry_price")
            stop = position.get("stop_loss")
            target = position.get("take_profit")
            opened = position.get("datetime", "")[:16]

            rows += _table_row("Entry price", _fmt_price(entry) + f" {bridge}")
            if stop:
                rows += _table_row("Stop-loss", _fmt_price(stop) + f" {bridge}", _C["red"])
            if target:
                rows += _table_row("Take-profit", _fmt_price(target) + f" {bridge}", _C["green"])
            if entry and (stop or target):
                sl_pct = ((stop / entry - 1) * 100) if entry and stop else None
                tp_pct = ((target / entry - 1) * 100) if entry and target else None
                if sl_pct is not None:
                    rows += _table_row("  stop at", _fmt_pct(sl_pct), _C["red"])
                if tp_pct is not None:
                    rows += _table_row("  target at", _fmt_pct(tp_pct), _C["green"])
            if opened:
                rows += _table_row("Opened", opened + " UTC")

        return _section("Current Position", rows)

    def _build_activity_html(self) -> str:
        """Activity log entries since the last email."""
        from .models import ActivityLog  # local import

        try:
            # Use elapsed time window that matches the interval (plus a small
            # buffer so nothing falls between two emails due to clock drift).
            interval_hours = self.db.get_email_interval_hours()
            since = datetime.utcnow() - timedelta(hours=interval_hours + 0.1)

            with self.db.db_session() as session:
                entries = (
                    session.query(ActivityLog)
                    .filter(ActivityLog.datetime >= since)
                    .order_by(ActivityLog.datetime.desc())
                    .limit(20)
                    .all()
                )
                rows_data = [e.info() for e in entries]
        except Exception:  # pylint: disable=broad-except
            return ""

        if not rows_data:
            empty = (
                f'<tr><td style="padding:12px;color:{_C["muted"]};font-size:13px">'
                f"No activity in the last {interval_hours}h</td></tr>"
            )
            return _section("Recent Activity", empty)

        rows_html = "".join(_activity_row(e) for e in rows_data)
        return _section(f"Activity (last {interval_hours}h)", rows_html)

    # ------------------------------------------------------------------- SMTP

    def _smtp_send(self, subject: str, html: str) -> str:
        """Dispatch via SMTP. Returns '' on success, error message on failure."""
        # Re-read EmailConfig on every send so credential rotations are picked
        # up without a restart — just update user.cfg and wait for next tick.
        from .email_config import EmailConfig
        ecfg = EmailConfig()

        host = ecfg.HOST
        port = ecfg.PORT
        user = ecfg.USERNAME
        password = ecfg.PASSWORD
        recipient = ecfg.RECIPIENT

        if not all([host, user, password, recipient]):
            return (
                "SMTP not fully configured. "
                "Set email_host, email_username, email_password, email_recipient in user.cfg."
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                    server.login(user, password)
                    server.sendmail(user, [recipient], msg.as_bytes())
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(user, password)
                    server.sendmail(user, [recipient], msg.as_bytes())
        except smtplib.SMTPAuthenticationError:
            return "SMTP authentication failed — check email_username and email_password."
        except smtplib.SMTPException as exc:
            return f"SMTP error: {exc}"
        except OSError as exc:
            return f"Network error reaching {host}:{port} — {exc}"

        return ""
