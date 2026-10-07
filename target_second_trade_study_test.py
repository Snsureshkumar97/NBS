#!/usr/bin/env python3
"""target_second_trade_study.walk on hand-made trades: the higher reward:risk applies only to a trade that is not the
day's first (per index / on any index / after a loss on that index), the first keeps today's bar, the live walk's own
rules still hold, and no rule is exactly today's walk."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

import stop_day_study as sds
import target_second_trade_study as ts

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

T = lambda hm, d="2026-10-07": pd.Timestamp(f"{d} {hm}", tz="Asia/Kolkata")
def tr(index, start, end, rr, net=-100.0, side="PE", via="stop", d="2026-10-07"):
    return {"index": index, "side": side, "when": T(start, d), "exit_time": T(end, d), "closed_via": via, "exit_adx": 15.0,
            "net": net, "rr": rr}
pick = lambda rows: [(r["index"], r["when"].strftime("%H:%M")) for r in rows]

# 7 Oct's shape: puts on Nifty 09:30 and Sensex 09:45 stopped; puts again at 13:15 with reward:risk 1.0
day = [tr("NIFTY", "09:30", "10:30", 1.65), tr("SENSEX", "09:45", "10:15", 1.79),
       tr("NIFTY", "13:15", "14:00", 1.0, net=200.0), tr("SENSEX", "13:15", "14:00", 1.1, net=300.0),
       tr("NIFTY", "09:30", "10:00", 1.0, d="2026-10-08")]
check("no rule: every trade (today's walk)", len(ts.walk(day)) == 5 and pick(ts.walk(day)) == pick(sds.per_index(day)))
r = ts.walk(day, 1.25, "index")
check("per index, 1.25: the 13:15 trades (each index's second) refused; the firsts kept, whatever their ratio",
      pick(r) == [("NIFTY", "09:30"), ("SENSEX", "09:45"), ("NIFTY", "09:30")], pick(r))
check("...and the next day's first trade at 1.0 is a first again", r[-1]["when"].day == 8)
anyr = ts.walk([tr("NIFTY", "09:30", "10:30", 1.65), tr("SENSEX", "09:45", "10:15", 1.1)], 1.25, "any")
check("any index, 1.25: Sensex's FIRST trade is refused because Nifty already traded", pick(anyr) == [("NIFTY", "09:30")], pick(anyr))
win_first = [tr("NIFTY", "09:30", "10:30", 1.65, net=500.0), tr("NIFTY", "13:15", "14:00", 1.0)]
check("after a loss: a second trade after a WIN keeps today's bar", len(ts.walk(win_first, 1.25, "loss")) == 2)
lost = [tr("NIFTY", "09:30", "10:30", 1.65, net=-500.0), tr("NIFTY", "13:15", "14:00", 1.0)]
check("...after a LOSS on that index it needs the higher bar", len(ts.walk(lost, 1.25, "loss")) == 1)
check("...and passes it with 1.5", len(ts.walk([lost[0], dict(lost[1], rr=1.5)], 1.25, "loss")) == 2)
other = [tr("SENSEX", "09:30", "10:30", 1.65, net=-500.0), tr("NIFTY", "13:15", "14:00", 1.0)]
check("...a loss on ANOTHER index does not raise this one's bar", len(ts.walk(other, 1.25, "loss")) == 2)
check("one position per index still holds under a rule", len(ts.walk([tr("NIFTY", "09:30", "13:30", 1.65), tr("NIFTY", "13:15", "14:00", 2.5)], 1.25, "index")) == 1)

print()
print("TARGET SECOND TRADE STUDY TEST PASSED" if not fails else f"TARGET SECOND TRADE STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
