#!/usr/bin/env python3
"""Daily market-regime watcher: BTC close vs 200-day SMA.

Fetches Kraken daily OHLC for BTC/USD, compares the latest *completed* daily
close to the 200-day SMA, and emails an alert when the relationship crosses
in either direction. State is kept in a small JSON file so the alert fires
once per cross, not once per day.

Designed to run from host cron (stdlib only, no third-party deps):
    15 0 * * * TZ=UTC /usr/bin/python3 /opt/bot/scripts/market_regime_watch.py \
        --env-file /opt/bot/.env --state-file /opt/bot/logs/market_regime_state.json \
        >> /opt/bot/logs/market_regime_watch.log 2>&1

SMTP settings are read from the env file: SMTP_SERVER, SMTP_PORT,
SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS, REPORT_SENDER, and the
recipient list from ALERT_EMAIL (fallback REPORT_RECIPIENTS).
"""
import argparse
import json
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

OHLC_URL = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440"
SMA_DAYS = 200
DAY = 86400


def load_env(path):
    env = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")
    return env


def fetch_closes():
    with urllib.request.urlopen(OHLC_URL, timeout=30) as resp:
        data = json.load(resp)
    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")
    rows = next(v for v in data["result"].values() if isinstance(v, list))
    now = datetime.now(timezone.utc).timestamp()
    # keep only completed daily bars (bar start + 24h in the past)
    return [(int(r[0]), float(r[4])) for r in rows if int(r[0]) + DAY <= now]


def send_email(env, subject, body):
    recipients = env.get("ALERT_EMAIL") or env.get("REPORT_RECIPIENTS") or ""
    recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    if not recipients:
        raise RuntimeError("no recipients: set ALERT_EMAIL or REPORT_RECIPIENTS")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = env.get("REPORT_SENDER", env.get("SMTP_USERNAME", ""))
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    host, port = env["SMTP_SERVER"], int(env.get("SMTP_PORT", 587))
    use_tls = env.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    with smtplib.SMTP(host, port, timeout=30) as s:
        if use_tls:
            s.starttls()
        if env.get("SMTP_USERNAME"):
            s.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="/opt/bot/.env")
    ap.add_argument("--state-file", default="/opt/bot/logs/market_regime_state.json")
    ap.add_argument("--dry-run", action="store_true", help="print status, never email")
    args = ap.parse_args()

    closes = fetch_closes()
    if len(closes) < SMA_DAYS:
        print(f"ERROR: only {len(closes)} completed bars, need {SMA_DAYS}")
        return 1
    bar_ts, close = closes[-1]
    sma = sum(c for _, c in closes[-SMA_DAYS:]) / SMA_DAYS
    status = "above" if close > sma else "below"
    bar_date = datetime.fromtimestamp(bar_ts, timezone.utc).date()

    state_path = Path(args.state_file)
    prev = None
    if state_path.exists():
        prev = json.loads(state_path.read_text()).get("status")

    line = (f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"bar={bar_date} close={close:.1f} sma{SMA_DAYS}={sma:.1f} "
            f"status={status} prev={prev}")
    print(line)

    crossed = prev is not None and prev != status
    if crossed and not args.dry_run:
        env = load_env(args.env_file)
        subject = f"[BotTrader] BTC {SMA_DAYS}d SMA cross: {prev.upper()} -> {status.upper()}"
        body = (
            f"BTC daily close crossed its {SMA_DAYS}-day SMA.\n\n"
            f"Completed bar: {bar_date}\n"
            f"Close: ${close:,.1f}\n"
            f"SMA{SMA_DAYS}: ${sma:,.1f}\n"
            f"Status: {prev} -> {status}\n\n"
            "Standing follow-ups on an upward cross:\n"
            "  - re-run the carry gauntlet (~5 min) on fresh data\n"
            "  - note the paper bot has never been observed in this regime\n"
        )
        send_email(env, subject, body)
        print(f"alert sent: {subject}")
    elif crossed:
        print("dry-run: cross detected, email suppressed")

    if not args.dry_run or not state_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(
            {"status": status, "bar_date": str(bar_date), "close": close,
             "sma": round(sma, 2),
             "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
