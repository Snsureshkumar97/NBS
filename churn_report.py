#!/usr/bin/env python3
"""
churn_report.py — why did a day take so many trades?

    python3 churn_report.py            # the most recent day in the log
    python3 churn_report.py 2026-09-01

Reads trades.csv and splits the day's closes by HOW they ended. The number
that matters is "signal changed": those are positions closed early because the
tool changed its mind, each one paying costs twice for one change of opinion.
"""
import collections
import sys

import trade_log
import trade_log as _tl


def main():
    rows = [r for r in trade_log._read_rows() if r.get("event") == "CLOSE"]
    if not rows:
        print("No completed trades in the log yet.")
        return 0
    day = sys.argv[1] if len(sys.argv) > 1 else max(r.get("date") or "" for r in rows)
    day_rows = [r for r in rows if (r.get("date") or "") == day]
    if not day_rows:
        print(f"Nothing logged for {day}.")
        return 0

    def bucket(status):
        s = (status or "").lower()
        if "signal changed" in s:
            return "signal changed (closed early)"
        if _tl.is_target_close(s):
            return "target reached"
        if "stop-loss" in s:
            return "stopped out"
        if "cleared" in s:
            return "cleared by hand"
        return "other"

    counts = collections.Counter(bucket(r.get("status")) for r in day_rows)
    pnl = collections.defaultdict(float)
    for r in day_rows:
        v = trade_log._f(r.get("pnl"))
        if v is not None:
            pnl[bucket(r.get("status"))] += v

    n = len(day_rows)
    print(f"\n{day} — {n} closed trade{'s' if n != 1 else ''}\n")
    print(f"  {'how it ended':32s} {'count':>6s} {'share':>7s} {'P&L':>12s}")
    print("  " + "-" * 60)
    for name, c in counts.most_common():
        print(f"  {name:32s} {c:6d} {100*c/n:6.0f}% {pnl[name]:12,.0f}")
    total = sum(pnl.values())
    print("  " + "-" * 60)
    print(f"  {'TOTAL':32s} {n:6d} {'':7s} {total:12,.0f}")

    churn = counts.get("signal changed (closed early)", 0)
    print()
    if churn:
        print(f"  {churn} of {n} were closed EARLY because the signal flipped.")
        print(f"  Those alone account for {pnl['signal changed (closed early)']:,.0f}.")
        print("  With CLOSE_ON_SIGNAL_FLIP = False (the new default) those")
        print("  positions are left alone to reach their own target or stop.")
    else:
        print("  Nothing was closed early by a change of signal.")

    by_index = collections.Counter(r.get("index") or "?" for r in day_rows)
    print("\n  by index: " + "  ".join(f"{k} {v}" for k, v in by_index.most_common()))
    hours = collections.Counter((r.get("time_ist") or "")[:2] for r in day_rows)
    if any(hours):
        busiest = hours.most_common(1)[0]
        print(f"  busiest hour: {busiest[0]}:00 with {busiest[1]} trades")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
