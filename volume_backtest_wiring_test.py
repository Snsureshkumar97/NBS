#!/usr/bin/env python3
"""backtest_intraday.run()'s new volume_by_date parameter - wiring only,
not re-deriving the whole backtest. Confirms: None (the default) behaves
exactly as before, a date with a reading reaches build_recommendation()
as {"available": True, "volume_score": N}, and a date WITHOUT one reaches
it as {"available": False} (never silently dropped, never crashes)."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import backtest_intraday as bt
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def fixture_df():
    rs = np.random.RandomState(7)
    n = 400
    idx = pd.date_range(end="2026-09-01 15:15", periods=n, freq="15min")
    close = 24000 + np.cumsum(rs.randn(n) * 9 + 1.0)
    op = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"Open": op, "High": np.maximum(op, close) + 8,
                         "Low": np.minimum(op, close) - 8, "Close": close,
                         "Volume": 0}, index=idx)


df = fixture_df()
seen_volume_args = []
real_build_rec = se.build_recommendation
def spy(*a, **kw):
    seen_volume_args.append(kw.get("volume"))
    return real_build_rec(*a, **kw)
se.build_recommendation = spy

try:
    print("1. volume_by_date=None (THE DEFAULT): EVERY CALL GETS volume=None, UNCHANGED FROM BEFORE")
    seen_volume_args.clear()
    bt.run("NIFTY", df)
    check("at least one bar was actually evaluated", len(seen_volume_args) > 0, len(seen_volume_args))
    check("every single call passed volume=None - nothing new happens by default",
          all(v is None for v in seen_volume_args), set(map(str, seen_volume_args[:5])))

    print("2. A DATE WITH A READING REACHES build_recommendation() AS A PROPER volume DICT")
    seen_volume_args.clear()
    by_date = {d: 1 for d in sorted(set(df.index.date))}       # every day reads +1
    bt.run("NIFTY", df, volume_by_date=by_date)
    check("at least one call now carries a real volume dict",
          any(v is not None for v in seen_volume_args), len(seen_volume_args))
    check("every one of them is available=True with the score looked up by that bar's own date",
          all(v == {"available": True, "volume_score": 1} for v in seen_volume_args
              if v is not None),
          [v for v in seen_volume_args if v is not None][:3])

    print("3. A DATE *WITHOUT* A READING REACHES build_recommendation() AS available=False, "
          "NOT SILENTLY DROPPED AND NOT A CRASH")
    seen_volume_args.clear()
    # the LAST day, not the first - bt.run()'s own warmup period skips past
    # the very start of the fixture entirely, so a reading pinned to day one
    # would never actually reach a bar the loop evaluates at all.
    only_one_day = {sorted(set(df.index.date))[-1]: 1}     # every OTHER day has nothing
    bt.run("NIFTY", df, volume_by_date=only_one_day)
    check("at least one call falls on a day with no reading",
          any(v == {"available": False} for v in seen_volume_args), len(seen_volume_args))
    check("and at least one call (the one day given) still carries a real reading",
          any(v == {"available": True, "volume_score": 1} for v in seen_volume_args),
          [v for v in seen_volume_args if v is not None][:3])
finally:
    se.build_recommendation = real_build_rec

print()
if fails:
    print(f"VOLUME BACKTEST WIRING TEST FAILED - {len(fails)}: " + "; ".join(fails))
    sys.exit(1)
print("VOLUME BACKTEST WIRING TEST PASSED")
