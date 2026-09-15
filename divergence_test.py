#!/usr/bin/env python3
"""The RSI-divergence entry filter: by hand, bar for bar against the tested rule,
and as a ticket hold. Real history is read from the cache; nothing live is touched."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

import backtest_intraday as bt
import config
import indicators as ind
import skills_study
import tickets

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. BY HAND")
c = np.full(30, 100.0); r = np.full(30, 50.0)
c[20], r[20] = 110.0, 70.0          # the previous high, 9 bars back
c[29], r[29] = 111.0, 65.0          # a new high on weaker RSI
check("bearish divergence blocks a CE", ind.rsi_divergence(c, r, "CE"))
check("...but not a PE", not ind.rsi_divergence(c, r, "PE"))
r2 = r.copy(); r2[29] = 69.0
check("RSI only 1 point weaker is not a divergence", not ind.rsi_divergence(c, r2, "CE"))
r3 = r.copy(); r3[29] = 75.0
check("RSI confirming the new high is not a divergence", not ind.rsi_divergence(c, r3, "CE"))
c4 = c.copy(); c4[29] = 109.0
check("no new closing high, no divergence", not ind.rsi_divergence(c4, r, "CE"))
cp = 200 - c; rp = 100 - r
check("the mirror: a new low on stronger RSI blocks a PE", ind.rsi_divergence(cp, rp, "PE") and not ind.rsi_divergence(cp, rp, "CE"))
check("too little history says nothing", not ind.rsi_divergence(c[:15], r[:15], "CE"))

print("2. THE LIVE FUNCTION IS THE TESTED RULE, BAR FOR BAR")
df = bt.fetch_history("NIFTY", years=3, use_cache=True).iloc[-6000:]
cl = df["Close"].to_numpy(); rs_ = ind.rsi(df["Close"], 14).to_numpy()
diff, hits = 0, {"CE": [], "PE": []}
for i in range(60, len(cl)):
    for side in ("CE", "PE"):
        a = ind.rsi_divergence(cl[:i + 1], rs_[:i + 1], side)
        b = skills_study.divergence(cl, rs_, i, side)
        diff += a != b
        if a:
            hits[side].append(i)
check("identical to skills_study.divergence on 6,000 Nifty bars, both sides", diff == 0, f"{diff} differ")
check("and it does fire on real data", len(hits["CE"]) > 5 and len(hits["PE"]) > 5, {k: len(v) for k, v in hits.items()})

print("3. AS A TICKET HOLD")
book = tickets.TicketBook(None, market="nse_index")
i = hits["CE"][0]
rec = {"index": "NIFTY", "option_type": "CE", "candles": df.iloc[:i + 1]}
h = book._divergence_hold(rec)
check("a CE into a real divergence is held, and says why", h and h[0] == "rsi_divergence" and "RSI" in h[2], h and h[1])
quiet = next(k for k in range(200, len(cl)) if k not in hits["CE"])
check("a CE with no divergence passes", book._divergence_hold({"index": "NIFTY", "option_type": "CE", "candles": df.iloc[:quiet + 1]}) is None)
check("Bitcoin is not held - the rule was tested on the indices", book._divergence_hold(dict(rec, index="BTC")) is None)
was = config.SKIP_RSI_DIVERGENCE; config.SKIP_RSI_DIVERGENCE = False
check("switched off, nothing is held", book._divergence_hold(rec) is None)
config.SKIP_RSI_DIVERGENCE = was
check("no candles, no opinion", book._divergence_hold({"index": "NIFTY", "option_type": "CE"}) is None)

print("DIVERGENCE TEST PASSED" if not fails else f"DIVERGENCE TEST FAILED: {fails}")
