#!/usr/bin/env python3
"""Watch the open and record whether the tool ran with nobody logged in.

Run by launchd across 09:15-09:35 IST. It answers one question: did the
always-on supervisor bring a feed up on its own, and did the rules issue
anything.

It never calls /api/state. That endpoint starts a feed as a side effect, so
asking it "is a feed running?" would make the answer yes - the measurement
would create the thing being measured. Everything here is read from the
server's own log, the trade log and the account store instead.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import zoneinfo

APP = "/Users/sureshkumar/CLAUDE WORKSPACE/trading-tool 4"
sys.path.insert(0, APP)
os.chdir(APP)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
SERVER_LOG = os.path.expanduser("~/Library/Logs/nbs-signal-tool.log")
SAMPLES = 21          # one a minute, 09:15 -> 09:35
INTERVAL = 60


def ist_now():
    return dt.datetime.now(IST)


def agents():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return {}
    got = {}
    for line in out.splitlines():
        if "com.nbs." in line:
            bits = line.split()
            got[bits[-1]] = bits[0]
    return got


def token_present(email):
    try:
        import user_kite
        return bool(user_kite.token_for(email))
    except Exception:
        return None


def resident_lines(today):
    """[resident] lines the server logged today."""
    try:
        with open(SERVER_LOG, "r", errors="replace") as f:
            # Indian only. Crypto is resident around the clock, so its start
            # line appears on most days and would otherwise be read as "the
            # Indian feed started by itself" - a pass for the wrong market.
            return [l.strip() for l in f
                    if "[resident]" in l and today in l and "[nse_index]" in l]
    except OSError:
        return []


def tickets_today(email, today):
    try:
        import trade_log
        rows = trade_log._read_rows(trade_log.user_log_path(email))
    except Exception:
        return []
    return [r for r in rows if r.get("date") == today and r.get("event") == "OPEN"]


def wait_for_open():
    """Sleep until 09:14 IST, then return.

    launchd fires this on the Mac's local clock, and the gap between that and
    IST moves twice a year when US daylight saving does. Rather than pin a
    local time that is right for half the year, the job is scheduled early and
    waits here on IST itself, which is the clock the market actually keeps.
    """
    target = ist_now().replace(hour=9, minute=14, second=0, microsecond=0)
    now = ist_now()
    if now > target:
        return 0.0           # already at or past the open; sample from here
    wait = (target - now).total_seconds()
    if wait > 4 * 3600:      # something is wrong with the clock; don't hang
        return 0.0
    time.sleep(wait)
    return wait


def main():
    import accounts
    now = ist_now()
    if now.weekday() >= 5:
        print(f"{now:%Y-%m-%d} is a weekend in IST — nothing to watch.")
        return
    waited = wait_for_open()
    users = accounts.always_on_users()
    email = users[0] if users else None
    today = ist_now().strftime("%Y-%m-%d")
    out_path = os.path.expanduser(
        f"~/Library/Logs/nbs-morning-check-{today}.log")

    lines = []

    def say(s):
        lines.append(s)
        print(s, flush=True)
        with open(out_path, "w") as f:
            f.write("\n".join(lines) + "\n")

    say(f"NBS morning check — {today}")
    say(f"started {ist_now():%H:%M:%S} IST"
        + (f" (waited {waited/60:.0f} min for the open)" if waited else ""))
    say(f"always-on accounts: {users or 'NONE — the toggle is off'}")
    if not email:
        say("\nNothing to watch. Turn on 'runs all session' and try again.")
        return

    say("")
    say(f"{'IST':>8}  {'agents':>6}  {'token':>5}  {'resident':>8}  {'tickets':>7}")
    say(f"{'-'*8}  {'-'*6}  {'-'*5}  {'-'*8}  {'-'*7}")

    first_seen = None
    for i in range(SAMPLES):
        now = ist_now()
        ag = agents()
        up = sum(1 for pid in ag.values() if pid not in ("-", ""))
        tok = token_present(email)
        res = resident_lines(today)
        tks = tickets_today(email, today)
        if res and first_seen is None:
            first_seen = res[0]
        say(f"{now:%H:%M:%S}  {up:>6}  {str(bool(tok)):>5}  "
            f"{len(res):>8}  {len(tks):>7}")
        if i < SAMPLES - 1:
            time.sleep(INTERVAL)

    res = resident_lines(today)
    tks = tickets_today(email, today)
    say("")
    say("VERDICT")
    say(f"  feed started unattended : {bool(res)}")
    if first_seen:
        say(f"    {first_seen}")
    say(f"  Zerodha token present   : {token_present(email)}")
    say(f"  tickets issued today    : {len(tks)}")
    for r in tks:
        say(f"    {r.get('time_ist')} {r.get('index')} {r.get('strike')}"
            f"{r.get('option_type')} entry {r.get('entry')}")
    if not res:
        say("")
        say("  No feed started. The usual causes, in order of likelihood:")
        say("    - the Mac was asleep (pmset says it sleeps after 1 minute)")
        say("    - Zerodha's token had not been reconnected after 07:30 IST")
        say("    - the launchd agents were not running")
    elif not tks:
        say("")
        say("  The feed ran and the rules issued nothing. That is a result,")
        say("  not a failure - most mornings do not produce a signal.")
    say("")
    say(f"finished {ist_now():%H:%M:%S} IST")


if __name__ == "__main__":
    main()
