"""
trade_log.py — a permanent record of every ticket, and a session summary
================================================================================
The window's SESSION strip lives in memory and dies when you close it. That
is fine if you're sitting there watching. It is useless if the market runs
while you're asleep — which, for anyone outside India, it does.

So every ticket is also appended to a CSV on disk the moment it opens and
again when it closes. The file survives restarts, crashes and reboots, opens
in Excel, and is the only thing that turns "I ran it for a few days" into
evidence you can actually judge the strategy by.

Two rows per trade:
  OPEN   written immediately when a ticket is issued — so even a crash
         mid-trade leaves proof the signal happened
  CLOSE  written when it finishes, with the outcome and P&L

Filter to CLOSE rows for analysis; the OPEN rows are the crash trail.
"""

import csv
import re
import os
import datetime as dt

CSV_NAME = "trades.csv"

FIELDS = [
    "trade_id", "event", "date", "time_ist", "index", "strike", "option_type",
    "entry", "exit", "t1", "t2", "t3", "stop",
    "t1_hit", "t2_hit", "t3_hit", "sl_hit",
    "status", "pnl", "lot_size", "lots", "tracked_on",
    "entry_spot", "risk_points", "reach_points", "reward_risk",
    "score", "confidence", "adx", "strictness",
]


LOG_DIR_NAME = "trading-tool-logs"


def log_dir():
    """A stable folder in your home directory — deliberately NOT next to the
    code.

    You unzip a fresh copy of the tool every time it's updated, which means
    anything stored beside the code gets stranded in the old folder. Trading
    history is the one thing that must never fragment like that: a month of
    evidence scattered across 'trading-tool 3', 'trading-tool 7' and
    'trading-tool 11' is worth almost nothing. Keeping it in ~/ means every
    copy of the tool, forever, appends to the same file.
    """
    try:
        d = os.path.join(os.path.expanduser("~"), LOG_DIR_NAME)
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        # Home unavailable (odd sandboxes, some Termux setups) — fall back to
        # sitting beside the code rather than losing the record entirely.
        return os.path.dirname(os.path.abspath(__file__))


def _log_path():
    return os.path.join(log_dir(), CSV_NAME)


def user_log_path(email):
    """A private trades.csv for one website account.

    The desktop app is one person at one machine, so it has always written to
    a single file. The website is not: a shared CSV would interleave three
    people's tickets into one history that describes nobody, and would let any
    of them read the others' trades through the daily-limit counters.

    Named by a hash rather than the address, so the folder listing is not a
    list of the site's users.
    """
    import hashlib
    key = hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:16]
    d = os.path.join(log_dir(), "users", key)
    try:
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        return _log_path()
    return os.path.join(d, CSV_NAME)


def _append(row, path=None):
    path = path or _log_path()
    new = not os.path.exists(path)
    try:
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
        return True
    except OSError:
        return False


def _base_row(trade, rec, now):
    targets = trade["premium_targets"] if trade["use_premium"] else trade["index_targets"]
    stop = trade["premium_sl"] if trade["use_premium"] else trade["index_sl"]
    entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
    r = rec or {}
    return {
        "trade_id": trade.get("trade_id", ""),
        "date": now.strftime("%Y-%m-%d"),
        "time_ist": now.strftime("%H:%M:%S"),
        "index": trade["index"],
        "strike": trade["strike"],
        "option_type": trade["option_type"],
        "entry": entry,
        "t1": targets[0] if targets else None,
        "t2": targets[1] if targets and len(targets) > 1 else None,
        "t3": targets[2] if targets and len(targets) > 2 else None,
        "stop": stop,
        "lot_size": trade.get("lot_size"),
        # How many lots the pnl column is quoted for. Without it a +6,000 row
        # is unreadable a week later — one lot on a good move, or five on a
        # mediocre one?
        "lots": trade.get("lots", 1),
        "tracked_on": "premium" if trade["use_premium"] else "index",
        "entry_spot": trade.get("entry_spot"),
        "risk_points": r.get("risk_points"),
        "reach_points": r.get("reach_points"),
        "reward_risk": r.get("reach_to_risk"),
        "score": r.get("score"),
        "confidence": r.get("confidence"),
        "adx": (r.get("technical") or {}).get("adx"),
        "strictness": r.get("strictness"),
    }


def log_open(trade, rec, now, path=None):
    row = _base_row(trade, rec, now)
    row.update(event="OPEN", status="OPEN")
    return _append(row, path)


def log_close(trade, rec, now, exit_price, pnl, path=None):
    row = _base_row(trade, rec, now)
    row.update(
        event="CLOSE",
        exit=exit_price,
        t1_hit=trade["hit"]["T1"], t2_hit=trade["hit"]["T2"], t3_hit=trade["hit"]["T3"],
        sl_hit=trade["sl_hit"],
        status=trade["status"],
        pnl=pnl,
    )
    return _append(row, path)


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------
def _read_rows(path=None):
    path = path or _log_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_target_close(status):
    """Did this trade end by reaching its target?

    Matched on the shape of the sentence rather than on the literal "T3",
    because which target closes a trade is a setting now (EXIT_AT_TARGET). A
    reader that looked for "t3" counted a T2 exit as a loss — and the daily
    win limit, the summary and the P&L colours all read this.
    """
    low = (status or "").lower()
    return "full target reached" in low or bool(re.search(r"\bt[123] hit\b", low))


def day_counts(date_str=None, path=None):
    """How much trading has already happened today, read off the CSV.

    From disk, not memory, for the same reason the P&L is: restarting the tool
    must not hand you a fresh set of daily limits. Counting OPEN events rather
    than CLOSE ones so a trade counts against the day's budget the moment it is
    taken, not whenever it happens to finish.

    Returns (tickets_issued, t3_wins, stop_outs).
    """
    rows = _read_rows(path)
    if not rows:
        return 0, 0, 0
    if date_str is None:
        dates = [r.get("date") or "" for r in rows]
        date_str = max(dates) if dates else ""
    issued = wins = stops = 0
    for r in rows:
        if (r.get("date") or "") != date_str:
            continue
        ev = (r.get("event") or "").upper()
        if ev == "OPEN":
            issued += 1
        elif ev == "CLOSE":
            status = (r.get("status") or "")
            if is_target_close(status):
                wins += 1
            elif "stop-loss" in status:
                stops += 1
    return issued, wins, stops


def booked_today(date_str=None, path=None):
    """Realised P&L for one date, per index, read back off the CSV.

    Read from DISK rather than kept in memory on purpose: restarting the tool
    at lunchtime must not reset the day to zero. The morning's trades happened
    whether or not this process was running when they closed.

    Returns (per_index: {index: rupees}, trade_count).
    """
    rows = [r for r in _read_rows(path) if r.get("event") == "CLOSE"]
    if not rows:
        return {}, 0
    if date_str is None:
        date_str = max(r.get("date") or "" for r in rows)
    out, n = {}, 0
    for r in rows:
        if (r.get("date") or "") != date_str:
            continue
        n += 1
        pnl = _f(r.get("pnl"))
        if pnl is None:
            continue
        key = r.get("index") or "?"
        out[key] = round(out.get(key, 0.0) + pnl, 2)
    return out, n


def build_summary(date_str=None, path=None):
    """Readable summary of one day's closed trades. Defaults to the most
    recent date present in the log, so running it any time after the session
    gives you that session."""
    rows = [r for r in _read_rows(path) if r.get("event") == "CLOSE"]
    if not rows:
        return "No completed trades recorded yet."

    if date_str is None:
        date_str = max(r["date"] for r in rows)
    day = [r for r in rows if r["date"] == date_str]
    if not day:
        return f"No completed trades recorded on {date_str}."

    lines = []
    lines.append("=" * 74)
    lines.append(f" SESSION SUMMARY — {date_str}")
    lines.append("=" * 74)

    pnls = [_f(r.get("pnl")) for r in day]
    priced = [p for p in pnls if p is not None]
    wins = [p for p in priced if p > 0]
    losses = [p for p in priced if p < 0]

    lines.append(f"Trades closed        : {len(day)}")
    if priced:
        net = sum(priced)
        lines.append(f"Won / lost           : {len(wins)} / {len(losses)}")
        lines.append(f"Net P&L (as traded)  : {'+' if net >= 0 else '-'}Rs.{abs(net):,.0f}")
        if wins:
            lines.append(f"Average win          : +Rs.{sum(wins)/len(wins):,.0f}")
        if losses:
            lines.append(f"Average loss         : -Rs.{abs(sum(losses)/len(losses)):,.0f}")
    else:
        lines.append("(no rupee P&L — these were tracked on the index, not a live premium)")

    def rate(field):
        hits = sum(1 for r in day if str(r.get(field, "")).lower() == "true")
        return f"{hits}/{len(day)}  ({100*hits/len(day):.0f}%)"

    lines.append("-" * 74)
    lines.append(f"Reached T1           : {rate('t1_hit')}")
    lines.append(f"Reached T2           : {rate('t2_hit')}")
    lines.append(f"Reached T3           : {rate('t3_hit')}")
    lines.append(f"Stopped out          : {rate('sl_hit')}")

    by_index = {}
    for r in day:
        by_index.setdefault(r["index"], []).append(_f(r.get("pnl")))
    lines.append("-" * 74)
    lines.append("By index:")
    for idx, vals in sorted(by_index.items()):
        got = [v for v in vals if v is not None]
        net = f"{'+' if sum(got) >= 0 else '-'}Rs.{abs(sum(got)):,.0f}" if got else "n/a"
        lines.append(f"  {idx:<12}{len(vals)} trade(s), net {net}")

    lines.append("-" * 74)
    lines.append("Every trade:")
    for r in day:
        p = _f(r.get("pnl"))
        pstr = (f"{'+' if p >= 0 else '-'}Rs.{abs(p):,.0f}" if p is not None else "n/a")
        lines.append(f"  {r['time_ist']}  {r['index']:<10} {r['strike']} {r['option_type']}  "
                     f"entry {r['entry']} -> exit {r.get('exit') or '?'}  {pstr}")
        lines.append(f"      {r.get('status','')}")

    lines.append("=" * 74)
    lines.append("Index-level and premium figures are estimates and exclude brokerage,")
    lines.append("STT and slippage. Compare against your actual Zerodha contract note.")
    lines.append("=" * 74)
    return "\n".join(lines)


def save_summary(date_str=None, path=None):
    """Writes the summary next to the log and returns its path."""
    text = build_summary(date_str, path)
    rows = [r for r in _read_rows(path) if r.get("event") == "CLOSE"]
    if date_str is None and rows:
        date_str = max(r["date"] for r in rows)
    date_str = date_str or dt.date.today().isoformat()
    out = os.path.join(log_dir(), f"summary_{date_str}.txt")
    try:
        with open(out, "w") as f:
            f.write(text)
        return out
    except OSError:
        return None
