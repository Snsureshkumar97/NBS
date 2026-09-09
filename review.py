#!/usr/bin/env python3
"""
review.py — what actually happened, read back from your own trade log
================================================================================
    python3 review.py                 # everything ever logged
    python3 review.py --days 7        # just the last week
    python3 review.py --index NIFTY
    python3 review.py --cost 0.15     # round-trip cost in R (default 0.15)

Reads ~/trading-tool-logs/trades.csv — the file the tool writes itself — and
answers the only question that matters after a week of running it: did this make
or lose money, and is there any pattern in when it worked.

Every number here is from YOUR trades, not a backtest. That makes it small (a
week is not many trades) but real. Treat the breakdowns as hints about where to
look, never as conclusions: slice twenty trades four ways and something will
look brilliant by luck alone. The sample-size warnings below are there because
that is the single easiest way to fool yourself.
"""

import argparse
import datetime as dt
import os
import sys
from collections import defaultdict

import trade_log

LINE = "=" * 74


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def r_multiple(row):
    """Result in R — how many times the risk was made or lost.

    R is the only unit that compares a Nifty trade to a BankNifty one, or a
    tight stop to a wide one. Rupees don't; a big win on a big stop can be a
    worse trade than a small win on a small one.
    """
    entry, exit_, stop = _f(row.get("entry")), _f(row.get("exit")), _f(row.get("stop"))
    if entry is None or exit_ is None or stop is None:
        return None
    on_premium = (row.get("tracked_on") == "premium")
    put = (row.get("option_type") == "PE")
    if on_premium or not put:
        # Premium always rises when the trade works, whichever side you took.
        # A CE tracked on the index behaves the same way.
        risk = entry - stop
        return (exit_ - entry) / risk if risk else None
    # A PE tracked on the INDEX profits when the index falls.
    risk = stop - entry
    return (entry - exit_) / risk if risk else None


def load(days=None, index=None):
    rows = [r for r in trade_log._read_rows() if r.get("event") == "CLOSE"]
    if index:
        rows = [r for r in rows if r.get("index") == index.upper()]
    if days:
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= cutoff]
    return rows


def hit(rows, field):
    return sum(1 for r in rows if str(r.get(field, "")).lower() == "true")


def block(title, rows, cost_r, indent=" "):
    """One summary block. Used for the whole set and for every slice."""
    n = len(rows)
    if not n:
        print(f"{indent}{title:<22} no trades")
        return
    rs = [x for x in (r_multiple(r) for r in rows) if x is not None]
    avg = sum(rs) / len(rs) if rs else None
    wins = sum(1 for x in rs if x > 0)
    pnls = [p for p in (_f(r.get("pnl")) for r in rows) if p is not None]
    net = sum(pnls) if pnls else None
    parts = [f"{n:>3} trades"]
    if rs:
        parts.append(f"win {100*wins/len(rs):>3.0f}%")
        parts.append(f"avg {avg:+.3f}R")
        parts.append(f"net of costs {avg - cost_r:+.3f}R")
    if net is not None:
        parts.append(f"{'+' if net >= 0 else '-'}Rs.{abs(net):,.0f}")
    flag = "   (too few to mean anything)" if n < 20 else ""
    print(f"{indent}{title:<22} " + "  ".join(parts) + flag)


def main():
    ap = argparse.ArgumentParser(description="Review your own logged trades.")
    ap.add_argument("--days", type=int, default=None, help="only the last N days")
    ap.add_argument("--index", default=None)
    ap.add_argument("--cost", type=float, default=0.15,
                    help="round-trip cost in R: brokerage + spread + theta. Default 0.15")
    args = ap.parse_args()

    path = trade_log._log_path()
    print(LINE)
    print(" YOUR TRADES")
    print(LINE)
    print(f" Reading {path}")

    if not os.path.exists(path):
        print("\n That file does not exist yet, which means no ticket has ever been")
        print(" issued. It is created the moment one fires — not when the tool starts")
        print(" and not when you save a summary. Nothing is broken.")
        print(LINE)
        return

    rows = load(args.days, args.index)
    if not rows:
        allrows = [r for r in trade_log._read_rows() if r.get("event") == "CLOSE"]
        opens = [r for r in trade_log._read_rows() if r.get("event") == "OPEN"]
        print(f"\n No CLOSED trades match. ({len(allrows)} closed in total, "
              f"{len(opens)} ever opened.)")
        if opens and not allrows:
            print(" Tickets have been issued but none has finished yet — a trade counts")
            print(" here only once it hits a target, a stop, or gets closed out.")
        print(LINE)
        return

    dates = sorted(r.get("date", "") for r in rows)
    print(f" {len(rows)} closed trade(s), {dates[0]} to {dates[-1]}")
    print(f" Costs assumed: {args.cost}R per round trip "
          f"(brokerage + option spread + time decay)")

    # ---- headline ---------------------------------------------------------
    print("\n" + LINE)
    print(" OVERALL")
    print(LINE)
    n = len(rows)
    for name, field in (("Reached T1", "t1_hit"), ("Reached T2", "t2_hit"),
                        ("Reached T3", "t3_hit"), ("Stopped out", "sl_hit")):
        h = hit(rows, field)
        print(f" {name:<16}{h:>3} of {n}   {100*h/n:>5.1f}%")
    block("", rows, args.cost, indent=" ")

    # ---- slices -----------------------------------------------------------
    def slice_by(title, keyfn, order=None):
        groups = defaultdict(list)
        for r in rows:
            k = keyfn(r)
            if k is not None:
                groups[k].append(r)
        if not groups:
            return
        print("\n" + LINE)
        print(f" BY {title}")
        print(LINE)
        keys = order(groups) if order else sorted(groups)
        for k in keys:
            block(str(k), groups[k], args.cost)

    slice_by("INDEX", lambda r: r.get("index"))
    slice_by("SIDE", lambda r: {"CE": "CE (calls)", "PE": "PE (puts)"}.get(r.get("option_type")))
    slice_by("DAY", lambda r: r.get("date"))

    def hour(r):
        t = r.get("time_ist") or ""
        return f"{t[:2]}:00 IST" if len(t) >= 2 and t[:2].isdigit() else None
    slice_by("HOUR OF DAY", hour)

    def adx_band(r):
        a = _f(r.get("adx"))
        if a is None:
            return None
        return "ADX 20-25" if a < 25 else ("ADX 25-35" if a < 35 else "ADX 35+")
    slice_by("TREND STRENGTH AT ENTRY", adx_band)

    def rr_band(r):
        v = _f(r.get("reward_risk"))
        if v is None:
            return None
        return "R:R under 1" if v < 1 else ("R:R 1-2" if v < 2 else "R:R 2+")
    slice_by("REWARD:RISK AT ENTRY", rr_band)

    # ---- verdict ----------------------------------------------------------
    rs = [x for x in (r_multiple(r) for r in rows) if x is not None]
    print("\n" + LINE)
    print(" WHAT IT MEANS")
    print(LINE)
    if not rs:
        print(" No trade had enough price detail to score. Nothing to conclude.")
    else:
        avg = sum(rs) / len(rs)
        netr = avg - args.cost
        print(f" Average result   {avg:+.3f} R per trade before costs")
        print(f" After costs      {netr:+.3f} R per trade")
        print(f" Over {len(rs)} trades that is {netr*len(rs):+.1f} R in total.\n")
        if len(rs) < 30:
            print(" WITH ONLY THIS MANY TRADES, THIS NUMBER IS NOISE. Thirty is not")
            print(" enough to tell a real edge from a run of luck in either direction.")
            print(" Keep running it; come back when there are a hundred.\n")
        if netr > 0.05:
            print(" Positive after costs. Worth continuing to watch.")
        elif netr > -0.05:
            print(" Break-even after costs, within the noise. No evidence either way.")
        else:
            print(" Negative after costs. Trading less would have done better than")
            print(" trading this.")
    print("\n Slices above are hints about where to look, not conclusions. Cut a small")
    print(" sample enough ways and one will always look good by chance. A pattern is")
    print(" worth acting on only if it survives on trades you found it AFTER.")
    print(LINE + "\n")


if __name__ == "__main__":
    main()
