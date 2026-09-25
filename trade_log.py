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
import json
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
    "cooldown_skipped",
    # Added 17 Sep 2026, at the user's request, so a closed trade's OWN entry
    # carries the reading that decided it - not just adx, which was already
    # here. A row from before this line existed simply has no value for these
    # three; that is a trade this can't be recovered for, not a bad read.
    # New columns always go at the very end - _upgrade_header() only ever
    # appends to an old header, and never reorders it.
    "rsi", "macd_hist", "vwap_gap",
    # Added 21 Sep 2026: the checklist beside the rule signal (signal_checks.py)
    # as it read when the ticket opened - how many checks agreed and how many
    # were against, and each one as key+symbol (+ agrees, - against, 0 neutral,
    # x no data) - so that whether it means anything can be studied later.
    "checks_agree", "checks_against", "checks",
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
    # Same reasoning as config.home_config_dir(): a hosted deployment has no
    # durable home directory, and a month of trade history is exactly the kind
    # of thing that must not be wiped by a redeploy.
    override = os.environ.get("TRADING_TOOL_LOGS", "").strip()
    if not override:
        base = os.environ.get("TRADING_TOOL_HOME", "").strip()
        override = os.path.join(base, LOG_DIR_NAME) if base else ""
    if override:
        try:
            os.makedirs(override, exist_ok=True)
            return override
        except OSError:
            pass
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


def user_log_path(email, market=None):
    """A private trades.csv for one website account.

    The desktop app is one person at one machine, so it has always written to
    a single file. The website is not: a shared CSV would interleave three
    people's tickets into one history that describes nobody, and would let any
    of them read the others' trades through the daily-limit counters.

    Named by a hash rather than the address, so the folder listing is not a
    list of the site's users.
    """
    import hashlib
    # The market is part of the identity. Rupee tickets on Nifty and dollar
    # tickets on BTC in one file would produce a net P&L that adds two
    # currencies together, and a hit rate mixing two different markets - both
    # numbers describing nothing. Separate files, separate records.
    ident = (email or "").strip().lower()
    if market and market != "nse_index":
        ident = f"{ident}#{market}"
    key = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]
    d = os.path.join(log_dir(), "users", key)
    try:
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        return _log_path()
    return os.path.join(d, CSV_NAME)


def _upgrade_header(path):
    """Give a log written before a column was added the current header.

    Without this a new row lands in an old file with one value more than the
    header names, and csv reads that value back under no name at all - the
    column would exist in the file and be invisible to every reader. Only a
    header that is exactly an earlier FIELDS (the same columns, in order, with
    the new ones missing from the end) is touched; anything else is left
    alone rather than guessed at. Rewritten to a temp file and swapped in, so
    a failure part-way leaves the original intact.
    """
    try:
        with open(path, newline="") as f:
            header = next(csv.reader(f), None)
        if not header or header == FIELDS:
            return
        if len(header) >= len(FIELDS) or header != FIELDS[:len(header)]:
            return
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        tmp = path + ".upgrade"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except (OSError, csv.Error):
        pass


def _append(row, path=None):
    path = path or _log_path()
    new = not os.path.exists(path)
    if not new:
        _upgrade_header(path)
    try:
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
        return True
    except OSError:
        return False


def _check_stamp(checks):
    try:
        import signal_checks
        return signal_checks.stamp(checks)
    except Exception:
        return (None, None, None)


def _base_row(trade, rec, now):
    targets = trade["premium_targets"] if trade["use_premium"] else trade["index_targets"]
    stop = trade["premium_sl"] if trade["use_premium"] else trade["index_sl"]
    entry = trade["entry_ltp"] if trade["use_premium"] else trade["entry_spot"]
    r = rec or {}
    tech = r.get("technical") or {}
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
        "adx": tech.get("adx"),
        "strictness": r.get("strictness"),
        "cooldown_skipped": "yes" if trade.get("cooldown_skipped") else "",
        # rsi is rounded here the same way feeds._public() rounds it for the live
        # card (tech only has the raw last_rsi); macd_hist and vwap_gap are
        # already rounded inside compute_technical_signal() and pass straight through.
        "rsi": round(tech["last_rsi"], 1) if tech.get("last_rsi") is not None else None,
        "macd_hist": tech.get("macd_hist"),
        "vwap_gap": tech.get("vwap_gap"),
        **dict(zip(("checks_agree", "checks_against", "checks"), _check_stamp(r.get("checks")))),
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
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    return _apply_fills(rows, path)


# --- what the brokers actually filled -----------------------------------------------------------------------------
# A ticket's entry and exit in this file are the tool's own premium prices. A LIVE order fills somewhere near them, and
# the executor writes the real average prices to a fills log beside this file when the position closes
# (live_orders / delta_orders: <log>.live.fills.jsonl, <log>.delta.fills.jsonl). The user (25 Sep 2026): the filled price
# should be what the tool shows - on the Signal page (real_entry.py) and in the Journal and Record. The file itself is
# append-only and stays exactly as written (the tool's own numbers, and the record of what it decided); what every
# reader of it is handed, here, is each closed live trade with its real entry, exit and result. Rows without a complete,
# consistent fill are handed over as written, so a partial or outside-sold position never mixes two sets of numbers.
_FILLS_CACHE = {}
_FILL_LOGS = ((".live.fills.jsonl", "zerodha"), (".delta.fills.jsonl", "delta"))


def _fills_for(path):
    """{trade_id: fill row + "venue"} for this log's live fills. Read again only when a fills file changed."""
    source = "ai" if os.path.basename(path).startswith("ai_") else "rule"
    out = {}
    stems = [path]
    beside = os.path.join(os.path.dirname(path), CSV_NAME)          # the AI desk's log has none of its own: the account's
    if beside != path:
        stems.append(beside)
    for stem in stems:
        for suffix, venue in _FILL_LOGS:
            fp = stem + suffix
            try:
                st = os.stat(fp)
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            hit = _FILLS_CACHE.get(fp)
            if hit is None or hit[0] != sig:
                rows = []
                try:
                    with open(fp) as fh:
                        for line in fh:
                            try:
                                r = json.loads(line)
                            except ValueError:
                                continue
                            if isinstance(r, dict):
                                rows.append(r)
                except OSError:
                    rows = []
                hit = _FILLS_CACHE[fp] = (sig, rows)
            for r in hit[1]:
                if (r.get("source") or "rule") == source and r.get("trade_id"):
                    out[r["trade_id"]] = dict(r, venue=venue)
    return out


def _fill_is_whole(f, close):
    """Everything bought was sold at known prices, none outside the tool, and it is the size the ticket was."""
    try:
        qty = float(f["qty"])
        if f.get("exit_avg") is None or f.get("gross_pnl") is None or f.get("entry_avg") is None:
            return False
        if float(f.get("exit_qty_priced") or 0) < qty or float(f.get("sold_outside_qty") or 0) > 0:
            return False
        lots, lot_size = float(close.get("lots") or 0), float(close.get("lot_size") or 0)
        want = lots if f.get("venue") == "delta" else lots * lot_size          # Delta counts contracts, Zerodha units
        return want > 0 and abs(qty - want) < 0.5
    except (TypeError, ValueError):
        return False


def _apply_fills(rows, path):
    fills = _fills_for(path)
    if not fills:
        return rows
    closes = {r.get("trade_id"): r for r in rows if r.get("event") == "CLOSE" and r.get("trade_id") in fills}
    whole = {tid for tid, c in closes.items() if _fill_is_whole(fills[tid], c)}
    if not whole:
        return rows
    out = []
    for r in rows:
        tid = r.get("trade_id")
        if tid not in whole or r.get("event") not in ("OPEN", "CLOSE"):
            out.append(r)
            continue
        f = fills[tid]
        r = dict(r)
        r["paper_entry"] = r.get("entry")
        r["entry"] = repr(round(float(f["entry_avg"]), 4))
        if r.get("event") == "CLOSE":
            r["paper_exit"], r["paper_pnl"] = r.get("exit"), r.get("pnl")
            r["exit"] = repr(round(float(f["exit_avg"]), 4))
            r["pnl"] = repr(round(float(f["gross_pnl"]), 2))
        r["filled"] = f["venue"]
        out.append(r)
    return out


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


def is_trailed_close(status):
    """Did a stop that had already been moved up to a target end this trade?

    The staircase trailing stop (tickets.TicketBook._check_price) makes a target
    the new stop the moment it is crossed, so a ticket that reached T1 and then
    came back to T1 closes as "stop-loss hit (trailed to T1)": a stop, in name,
    that exits in profit."""
    return "stop-loss hit (trailed to" in (status or "").lower()


def is_stop_out(status, pnl=None):
    """Did this trade end by losing to its stop - the only thing "stopped out"
    should count?

    Not every close with "stop-loss" in its status is one. Since 22 Sep 2026 the
    stop climbs as targets are crossed, so a trade can reach T1, fall back to T1
    and close on "stop-loss hit" having made money. Read by the words alone that
    was a loss: it was counted as stopped out on the Signal page, filed under
    "Stop" in the Journal's review, and shown to the AI as a stop. The money
    decides where it is known; where it is not (a ticket tracked on the index has
    no rupee figure) the "trailed to" in the status says the stop had climbed
    past the entry first. A real broker stop that had trailed is caught by its
    profit alone, since its status does not say so."""
    if "stop-loss" not in (status or "").lower():
        return False
    if pnl is not None:
        return pnl <= 0
    return not is_trailed_close(status)


def day_outcomes(date_str=None, path=None):
    """(tickets issued, ran to target, stopped out at a loss, closed in profit
    by a trailed stop) for one date - the same read as day_counts, with the
    fourth kind of ending it used to file under "stopped out"."""
    rows = _read_rows(path)
    if not rows:
        return 0, 0, 0, 0
    if date_str is None:
        dates = [r.get("date") or "" for r in rows]
        date_str = max(dates) if dates else ""
    issued = wins = stops = locked = 0
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
            elif "stop-loss" in status.lower():
                if is_stop_out(status, _f(r.get("pnl"))):
                    stops += 1
                else:
                    locked += 1
    return issued, wins, stops, locked


def day_counts(date_str=None, path=None):
    """How much trading has already happened today, read off the CSV.

    From disk, not memory, for the same reason the P&L is: restarting the tool
    must not hand you a fresh set of daily limits. Counting OPEN events rather
    than CLOSE ones so a trade counts against the day's budget the moment it is
    taken, not whenever it happens to finish.

    Returns (tickets_issued, t3_wins, stop_outs) - stop-outs that lost money;
    a stop that closed in profit is in day_outcomes' fourth number instead.
    """
    issued, wins, stops, _locked = day_outcomes(date_str, path)
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
    stopped = sum(1 for r in day if is_stop_out(r.get("status"), _f(r.get("pnl"))))
    trailed = sum(1 for r in day if "stop-loss" in (r.get("status") or "").lower()
                  and not is_stop_out(r.get("status"), _f(r.get("pnl"))))
    lines.append(f"Stopped out at a loss: {stopped}/{len(day)}  ({100*stopped/len(day):.0f}%)")
    lines.append(f"Trailed out in profit: {trailed}/{len(day)}  ({100*trailed/len(day):.0f}%)")

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
