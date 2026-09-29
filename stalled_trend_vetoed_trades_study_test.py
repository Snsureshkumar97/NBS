"""stalled_trend_vetoed_trades_study.py's own new logic: period(), summarise() and fmt_summary().
The backtest machinery it calls (ps.simulate/ps.price/bt.run, sts.stalled_series) is already
tested elsewhere - this only checks the partition-and-aggregate arithmetic added here, calling
the module's real functions directly rather than a copy that could silently drift from them."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stalled_trend_vetoed_trades_study as sv

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def row(when, net):
    return {"when": pd.Timestamp(when, tz="Asia/Kolkata"), "net": net}


print("1. period() KEEPS ONLY ROWS IN [lo, hi) - HALF-OPEN, MATCHING SPLIT'S OWN CONVENTION")
rows = [row("2025-08-14", 100), row("2025-08-15", 200), row("2025-08-16", 300)]
lo, hi = pd.Timestamp("2025-08-15", tz="Asia/Kolkata"), pd.Timestamp("2025-08-16", tz="Asia/Kolkata")
sel = sv.period(rows, lo, hi)
check("lo is included, hi is excluded", [r["net"] for r in sel] == [200], [r["net"] for r in sel])
check("period() on an empty list returns an empty list", sv.period([], lo, hi) == [])

print("2. summarise() AGGREGATES WINS/LOSSES CORRECTLY")
mixed = [row("2025-08-15", 500), row("2025-08-15", -100), row("2025-08-15", 300), row("2025-08-15", -50)]
s = sv.summarise(mixed)
check("total is the sum of every row", s["total"] == 650, s["total"])
check("win rate counts only strictly-positive rows", s["win_rate"] == 50.0, s["win_rate"])
check("avg win is the mean of the winners only", s["avg_win"] == 400.0, s["avg_win"])
check("avg loss is the mean of the losers only (net<=0 counted as a loss)", s["avg_loss"] == -75.0, s["avg_loss"])
check("a trade that breaks exactly even is a loss, not a win",
      sv.summarise([row("2025-08-15", 0)])["win_rate"] == 0.0)

print("3. EMPTY INPUT DOESN'T CRASH")
check("summarise([]) is None rather than dividing by zero", sv.summarise([]) is None)
check("fmt_summary(None) prints 'no trades'", sv.fmt_summary(None) == "no trades")
check("fmt_summary() of a real summary mentions the trade count and total",
      "4 trades" in sv.fmt_summary(s) and "650" in sv.fmt_summary(s), sv.fmt_summary(s))

print()
print("STALLED TREND VETOED TRADES STUDY TEST PASSED" if not fails
      else f"STALLED TREND VETOED TRADES STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
