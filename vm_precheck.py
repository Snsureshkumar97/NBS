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
SIDECAR_SUFFIXES = (".live.json", ".delta.json", ".exness_live.json")   # Kite/NSE, Delta, Exness (3 Oct 2026)
# An Exness order that never went through is recorded "failed" - there is no position behind it.
SAFE_STATES = {".exness_live.json": {"closed", "failed"}}
# A Zerodha entry that "failed" with nothing filled: safe only when no order reached Zerodha, or Zerodha itself said the
# order is CANCELLED / REJECTED (live_orders records it as entry_status). A failed entry whose cancel was never
# confirmed may still fill, so it keeps blocking (6 Oct 2026: an unfilled 09:57 entry blocked the 15:40 deploy).
KITE_DEAD = ("CANCELLED", "REJECTED")


def _kite_failed_safe(pos):
    """live_orders writes entry_order_id and filled_qty into every position it creates; a record without them is not
    one this can read, and blocks like any unknown state."""
    if pos.get("state") != "failed" or "filled_qty" not in pos or "entry_order_id" not in pos:
        return False
    try:
        if int(pos["filled_qty"] or 0) != 0:
            return False
    except (TypeError, ValueError):
        return False
    return pos["entry_order_id"] is None or pos.get("entry_status") in KITE_DEAD


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


def real_positions(account_dir, acks=()):
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
        safe = SAFE_STATES.get(suffix, {SAFE_STATE})
        for trade_id, pos in positions.items():
            state = (pos or {}).get("state") if isinstance(pos, dict) else None
            if state in safe or trade_id in acks:
                continue
            if suffix == ".live.json" and isinstance(pos, dict) and _kite_failed_safe(pos):
                continue
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


def check(acks=()):
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
        reals = real_positions(d, acks)
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
    for a in acks:
        lines.append(f"ACKNOWLEDGED by the operator (checked at the broker, not by this script): {a}")
    lines.append("CLEAR TO RESTART" if ok else "BLOCKED - not restarting")
    return ok, lines


VM_HOST = "nbs-signal-tool"
VM_COMMAND = ("gcloud compute ssh nbs-signal-tool --zone=asia-south1-a --project=nbs-signal-tool-4821 --quiet "
              "--command='cd ~/nbs && .venv/bin/python vm_precheck.py'")


def on_the_vm():
    """This reads the trade logs of the machine it runs on. Run on the Mac it read the Mac's stale copy from
    before the move to Google Cloud and printed CLEAR TO RESTART all through 5 Oct 2026 - the VM's own open gold
    ticket was never listed. Only the VM's answer means anything."""
    import socket
    return socket.gethostname().split(".")[0] == VM_HOST or os.environ.get("VM_PRECHECK_ANYWHERE") == "1"


if __name__ == "__main__":
    if not on_the_vm():
        print("NOT ON THE VM - this machine's trade logs are not the server's. Run it there:\n  " + VM_COMMAND)
        print("BLOCKED - not restarting")
        sys.exit(2)
    acks = tuple(sys.argv[sys.argv.index("--ack") + 1:]) if "--ack" in sys.argv else ()
    ok, lines = check(acks)
    print("\n".join(lines))
    sys.exit(0 if ok else 1)
