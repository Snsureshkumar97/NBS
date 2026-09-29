"""stalled_trend_filter_study.py: the wiring, and - the load-bearing claim of the whole study -
that stalled_series() really does match what signal_engine.compute_market_trend() calls stalled
at the same bar. If those two ever drifted apart, the study would be testing a veto that isn't
the one the trend panel actually describes."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import signal_engine as se
import stalled_trend_filter_study as sts

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE WIRING: A REAL AND, NEITHER SIDE ALONE IS ENOUGH")
calls = []
def fake_live_gate(k, i, rec, df):
    calls.append((k, i))
    return i % 2 == 0
orig = sts.res.live_gate
sts.res.live_gate = fake_live_gate
try:
    stalled_arr = np.array([False, False, True, True])
    check("live gate true (even i), not stalled -> True",
          sts.stalled_gate("NIFTY", 0, {}, None, stalled_arr) is True)
    check("live gate false (odd i) -> False regardless of stalled",
          sts.stalled_gate("NIFTY", 1, {}, None, stalled_arr) is False)
    check("live gate true (even i), but stalled -> False",
          sts.stalled_gate("NIFTY", 2, {}, None, stalled_arr) is False)
finally:
    sts.res.live_gate = orig

print("2. stalled_series() MATCHES compute_market_trend()'S OWN stalled FLAG, BAR FOR BAR")
# Real NIFTY history (already cached from every other study this session) - not synthetic, so
# the ADX/ATR/EMA warmup behaves exactly as it does live.
# regime_study.features()'s own 'stalled' column was tried first and DID NOT match - it leaves
# out config.ADX_DX_SMOOTHING, the per-market ADX smoothing compute_market_trend() applies. That
# is why the study computes its own stalled_series() instead of reusing regime_study's column;
# this checks that recomputation is the one that actually agrees.
df = bt.fetch_history("NIFTY", years=3, use_cache=True)
st = sts.stalled_series(df, "NIFTY")

warmup = max(config.EMA_SLOW, config.ADX_LENGTH * 2, 60) + 5
# Every 97th bar (an odd stride, so it does not land on the same weekday/time every check).
sample = list(range(warmup, len(df) - 1, 97))
check("enough bars survive to be a real check", len(sample) >= 20, len(sample))
mismatches = []
both_true = 0
for i in sample:
    sub = df.iloc[:i + 1]
    mt = se.compute_market_trend(sub, "NIFTY")
    if bool(mt["stalled"]) != bool(st[i]):
        mismatches.append((i, mt["stalled"], bool(st[i]), mt.get("label")))
    if mt["stalled"] and st[i]:
        both_true += 1
check("every sampled bar agrees with compute_market_trend()'s own stalled flag",
      not mismatches, mismatches[:5])
check("...and it is not a trivial always-False match: some bars really are stalled",
      both_true > 0, both_true)

print("3. threshold RAISES OR LOWERS HOW MANY BARS COUNT AS STALLED, IN THE RIGHT DIRECTION")
# A higher threshold means a bigger net move still counts as "hasn't gone anywhere", so it must
# flag the SAME bars a lower threshold does, plus more - never fewer. Checked as a strict subset,
# not just a bigger count, so a formula that flagged a DIFFERENT set of the same size would fail.
sweep = (0.5, 1.0, 1.5, 2.0)
arrs = {th: sts.stalled_series(df, "NIFTY", threshold=th) for th in sweep}
counts = {th: int(arrs[th].sum()) for th in sweep}
for lo, hi in zip(sweep, sweep[1:]):
    check(f"{lo} ATR's stalled bars are a subset of {hi} ATR's", np.all(arrs[lo] <= arrs[hi]))
check("stricter (lower) thresholds flag fewer or equal bars, monotonically",
      counts[0.5] <= counts[1.0] <= counts[1.5] <= counts[2.0], counts)
check("threshold=None falls back to config.TREND_MIN_DISPLACEMENT_ATR",
      np.array_equal(sts.stalled_series(df, "NIFTY"),
                     sts.stalled_series(df, "NIFTY", threshold=config.TREND_MIN_DISPLACEMENT_ATR)))

print()
print("STALLED TREND FILTER STUDY TEST PASSED" if not fails
      else f"STALLED TREND FILTER STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
