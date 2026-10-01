#!/usr/bin/env python3
"""vm_precheck.py — may the server be restarted right now?

Run before every deploy: `python3 vm_precheck.py`. Prints its findings and exits 0
("CLEAR TO RESTART") when nothing blocks, or 1 ("BLOCKED - not restarting") the
instant any account has a REAL position that is not confirmed closed. A real
position is live money at a broker; restarting the server mid-trade risks losing
track of it while it is still open. A paper position never blocks - it is the
tool's own bookkeeping, and nothing bad happens to it if the process restarts.

REWRITE NOTE (1 Oct 2026): the original /tmp/vm_precheck.py this project's deploy
practice relied on all through the prior day was lost when the VM was fully stopped
and started for a machine-type change (n2-standard-4) - /tmp on this box is tmpfs,
RAM-backed, wiped by a real power cycle in a way an ordinary `systemctl restart`
never touches. Nothing of the original source survived anywhere - not in git, not
in a saved note - so this is a REWRITE from the OBSERVED BEHAVIOUR relied on
throughout that day (the exact "CLEAR TO RESTART" / "open today: ... (does not
block)" / "REAL POSITION - BLOCKS: ..." / "BLOCKED - not restarting" output), not a
restoration of the original bytes. Written deliberately fail-safe throughout:
anything this cannot positively confirm is closed is treated as still open.

WHAT IT CHECKS
    Every account directory under trade_log.log_dir()/users/<hash>/ - one hash per
    (email, market) pair, exactly as trade_log.user_log_path() creates them, so this
    never needs its own, possibly-drifting copy of that path logic.

    REAL positions - the only thing that blocks - come from the broker-facing state
    files live_orders.py and delta_orders.py each maintain beside trades.csv:
    <trades.csv path>.live.json (Kite/NSE) and .delta.json (Delta/crypto), each a
    {"positions": {trade_id: {..., "state": ...}}} dict (see Executor._save() in
    either file). Only "state": "closed" is treated as safe; every other state
    those two files' own order-management code uses ("placing", "entering",
    "failed", "attention", "exiting", "open") blocks - and so does any state a
    future change introduces that this script does not recognise, on the same
    fail-safe reasoning. A missing sidecar file is the NORMAL case (most accounts
    have never enabled live orders) and is not a problem; a sidecar file that
    EXISTS but cannot be parsed blocks too - a position this cannot read might
    still be open.

    PAPER trades (informational only, never block) come from trades.csv itself: an
    OPEN event with no matching CLOSE event for that trade_id, and not already
    counted as a real position above.
"""
import csv
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trade_log

SAFE_STATE = "closed"
SIDECAR_SUFFIXES = (".live.json", ".delta.json")      # Kite/NSE, Delta/crypto


def now_ist():
    """IST, computed without relying on the host's own timezone. The VM this runs
    on over SSH defaults to UTC even though the trading tool's own systemd process
    sets TZ=Asia/Kolkata - an ad-hoc script here has neither, so this must not
    assume one (the same fix every other SSH script this project runs needed)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def account_dirs():
    """Every per-(email, market) account directory under the trade log's users/
    folder, sorted so repeated runs print accounts in the same order."""
    base = os.path.join(trade_log.log_dir(), "users")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d))


def real_positions(account_dir):
    """Every REAL position this account's broker-facing state files know about
    that is not confirmed closed - a list of (sidecar filename, trade_id, state).
    Reads <trades.csv>.live.json and <trades.csv>.delta.json, whichever exist."""
    out = []
    for suffix in SIDECAR_SUFFIXES:
        path = os.path.join(account_dir, trade_log.CSV_NAME + suffix)
        if not os.path.exists(path):
            continue
        fname = trade_log.CSV_NAME + suffix
        try:
            with open(path) as f:
                data = json.load(f)
            positions = data.get("positions") or {}
        except (OSError, ValueError):
            # Unreadable, not absent - fail toward blocking, not toward ignoring it.
            out.append((fname, "<unreadable sidecar file>", "unknown"))
            continue
        for trade_id, pos in positions.items():
            state = (pos or {}).get("state") if isinstance(pos, dict) else None
            if state != SAFE_STATE:
                out.append((fname, trade_id, state))
    return out


def open_paper_trades(account_dir, blocked_ids):
    """Every trade in this account's trades.csv that is OPEN with no matching
    CLOSE and is not already one of the real, blocking positions passed in -
    a list of (trade_id, date, time_ist, index, strike, option_type, market).
    Purely informational: nothing here ever blocks a restart."""
    path = os.path.join(account_dir, trade_log.CSV_NAME)
    if not os.path.exists(path):
        return []
    opens, closed_ids = {}, set()
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                tid = row.get("trade_id")
                if not tid:
                    continue
                if row.get("event") == "OPEN":
                    opens[tid] = row
                elif row.get("event") == "CLOSE":
                    closed_ids.add(tid)
    except OSError:
        return []
    out = []
    for tid, row in opens.items():
        if tid in closed_ids or tid in blocked_ids:
            continue
        index = row.get("index") or ""
        market = "crypto" if index in ("BTC", "GOLD") else "nse_index"
        out.append((tid, row.get("date"), row.get("time_ist"), index,
                    row.get("strike"), row.get("option_type"), market))
    return out


def check():
    """(ok, lines) for the whole server - ok is False the instant any account has
    a real, non-closed position; lines is every line this prints, in the same
    order, for a caller that wants the text without re-running the scan. The very
    last line is always the verdict, "CLEAR TO RESTART" or "BLOCKED - not
    restarting" - a caller only checking the tail still gets the real answer."""
    today = now_ist().strftime("%Y-%m-%d")
    lines = []
    ok = True
    for d in account_dirs():
        short = os.path.basename(d)[:6]
        reals = real_positions(d)
        by_file = {}
        for fname, tid, state in reals:
            by_file.setdefault(fname, []).append(tid)
        for fname, ids in by_file.items():
            ok = False
            lines.append(f"REAL POSITION - BLOCKS: {short} {fname} {ids!r}")
        blocked_ids = {tid for _, tid, _ in reals}
        for tid, date, time_ist, index, strike, option_type, market in open_paper_trades(d, blocked_ids):
            when = "today" if date == today else (date or "an unknown day")
            lines.append(f"open {when}: {short} trades.csv {index} {strike} {option_type} "
                        f"at {time_ist} -> {market} paper (does not block)")
    lines.append("CLEAR TO RESTART" if ok else "BLOCKED - not restarting")
    return ok, lines


if __name__ == "__main__":
    ok, lines = check()
    print("\n".join(lines))
    sys.exit(0 if ok else 1)
