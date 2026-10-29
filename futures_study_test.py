#!/usr/bin/env python3
"""futures_study's booking on made-up trades: points the trade's way, one lot, Zerodha's futures charges and the
slippage both ways. No network."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

import config
import futures_study as fs

qty = config.INSTRUMENTS["NIFTY"]["lot_size"]
c = fs.charges(25000.0, 25050.0, qty, 0.0)
buy, sell = 25000.0 * qty, 25050.0 * qty
want = 40 + 0.0002 * sell + 0.0000173 * (buy + sell) + 1e-6 * (buy + sell) + 0.00002 * buy \
    + 0.18 * (40 + 0.0000173 * (buy + sell) + 1e-6 * (buy + sell))
check("charges: brokerage, STT on the sell, exchange, SEBI, stamp on the buy, GST", abs(c - want) < 1e-6, (c, want))
check("slippage 0.01% a side adds 0.01% of both legs", abs(fs.charges(25000.0, 25050.0, qty, 0.0001) - c
                                                           - 0.0001 * (buy + sell)) < 1e-6)
ce = fs.booked([{"side": "CE", "entry_spot": 25000.0, "exit_spot": 25050.0}], "NIFTY", 0.0)[0]
pe = fs.booked([{"side": "PE", "entry_spot": 25000.0, "exit_spot": 25050.0}], "NIFTY", 0.0)[0]
check("a call's trade (long) makes the rise", ce["pts"] == 50.0 and abs(ce["net"] - (50 * qty - c)) < 1e-6, ce["net"])
check("a put's trade (short) loses it", pe["pts"] == -50.0 and abs(pe["net"] - (-50 * qty - c)) < 1e-6, pe["net"])
check("charges on a Nifty lot are a few hundred rupees, not thousands", 100 < c < 1000, round(c))

print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
