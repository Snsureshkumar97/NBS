#!/usr/bin/env python3
"""feeds._shared_analysis: the per-second indicator recompute shared between feeds that hold the SAME candles.
Every crypto account's feed reads the same shared Exness candles and live bar, and each recomputed the same
BTC/gold analysis every second - with the Indian feeds in market hours that held the GIL and made every page
wait (py-spy, 6 Oct 2026: 78% of the server's time in five feeds' live analysis)."""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import feeds
import signal_engine

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def frame(n=300, seed=1, last_close=None):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    idx = pd.date_range("2026-10-01 09:15", periods=n, freq="15min", tz="Asia/Kolkata")
    df = pd.DataFrame({"Open": close - 0.1, "High": close + 0.4, "Low": close - 0.4, "Close": close,
                       "Volume": rng.integers(100, 200, n).astype(float)}, index=idx)
    if last_close is not None:
        df.iloc[-1, df.columns.get_loc("Close")] = last_close
    return df

calls = {"tech": 0, "trend": 0}
real_tech, real_trend = signal_engine.compute_technical_signal, signal_engine.compute_market_trend
signal_engine.compute_technical_signal = lambda df, k=None: (calls.__setitem__("tech", calls["tech"] + 1) or real_tech(df, k))
signal_engine.compute_market_trend = lambda df, k=None: (calls.__setitem__("trend", calls["trend"] + 1) or real_trend(df, k))
CLOCK = {"t": 1_790_000_000.2}
real_clock = feeds._clock
feeds._clock = lambda: CLOCK["t"]
try:
    feeds._ANALYSIS_CACHE.clear()
    a = frame()
    b = a.copy()                                     # another account's feed: the same candles, its own frame
    t1, tr1 = feeds._shared_analysis("BTC", a)
    t2, tr2 = feeds._shared_analysis("BTC", b)
    t3, tr3 = feeds._shared_analysis("BTC", a.copy())
    check("three feeds with the same candles: the indicators are computed ONCE", calls == {"tech": 1, "trend": 1}, calls)
    check("...and every feed gets exactly what its own computation would give", t1 == real_tech(a, "BTC") and t2 == t1
          and tr1 == real_trend(a, "BTC") and tr3 == tr1)
    t2["adx"] = -1.0
    if isinstance(tr2, dict):
        tr2["direction"] = "CHANGED"
    t4, tr4 = feeds._shared_analysis("BTC", a.copy())
    check("each feed gets its OWN copy - changing one never reaches the next", t4["adx"] != -1.0
          and (not isinstance(tr4, dict) or tr4.get("direction") != "CHANGED"))
    def after_prime(variant, k="BTC", later=1.0):
        """Prime the cache with the ORIGINAL candles, then ask for `variant` `later` seconds on (default: the next
        second): True when it was computed afresh."""
        CLOCK["t"] = float(int(CLOCK["t"]) + 10) + 0.2
        feeds._ANALYSIS_CACHE.clear()
        feeds._shared_analysis("BTC", a, owner="feed-A")           # one account's feed...
        n = calls["tech"]
        CLOCK["t"] += later
        feeds._shared_analysis(k, variant, owner="feed-B")         # ...then another account's
        return calls["tech"] == n + 1
    moved = frame(last_close=float(a["Close"].iloc[-1]) + 3.0)
    check("the live bar's close moved: computed afresh", after_prime(moved))
    t5, _ = feeds._shared_analysis("BTC", moved)
    check("...with the right answer", t5 == real_tech(moved, "BTC"))
    high = a.copy()
    high.iloc[-1, high.columns.get_loc("High")] += 2.0          # a new high in the forming candle, close unchanged
    check("only the live bar's HIGH moved (close and volume unchanged): computed afresh", after_prime(high))
    check("a feed still on the previous candle: computed afresh (never another feed's newer answer)", after_prime(a.iloc[:-1]))
    check("the same candles for ANOTHER instrument: its own computation", after_prime(a.copy(), "GOLD"))
    early = a.copy()
    early.iloc[-30, early.columns.get_loc("Close")] += 1.0     # an earlier candle differs (one feed's history refreshed)
    check("a difference in an EARLIER candle (30 back): computed afresh", after_prime(early))
    evol = a.copy()
    evol.iloc[-30, evol.columns.get_loc("Volume")] += 50.0
    check("...or in an earlier candle's volume", after_prime(evol))
    vol = a.copy()
    vol.iloc[-1, vol.columns.get_loc("Volume")] += 50
    check("...and in the live bar's volume (the volume vote reads it)", after_prime(vol))

    print("  SAME-SECOND SHARING (the user, 6 Oct 2026: 'yes go ahead with the same second sharing')")
    check("ANOTHER feed's live bar a few ticks apart, in the SAME second: shared, not recomputed",
          not after_prime(moved, later=0.5) and not after_prime(high, later=0.5))
    check("...the same in the next second: computed afresh", after_prime(moved, later=1.0))
    check("an unchanged live bar a second later: still shared (nothing changed)", not after_prime(a.copy(), later=3.0))
    check("same second but an EARLIER candle differs: computed afresh (only the live bar may differ)",
          after_prime(early, later=0.5) and after_prime(evol, later=0.5))
    check("same second, a feed one candle behind: computed afresh", after_prime(a.iloc[:-1], later=0.5))
    check("same second, another instrument: its own", after_prime(a.copy(), "GOLD", later=0.5))
    CLOCK["t"] = float(int(CLOCK["t"]) + 10) + 0.1
    feeds._ANALYSIS_CACHE.clear()
    feeds._shared_analysis("BTC", a, owner="feed-A")
    n = calls["tech"]
    CLOCK["t"] += 0.5
    feeds._shared_analysis("BTC", moved, owner="feed-A")
    check("the SAME feed asking again in the same second with a moved live bar: computed afresh (never its own older answer)",
          calls["tech"] == n + 1, calls)
    feeds._shared_analysis("BTC", high, owner="feed-B")
    check("...while ANOTHER feed in that second shares it", calls["tech"] == n + 1, calls)
finally:
    signal_engine.compute_technical_signal, signal_engine.compute_market_trend = real_tech, real_trend
    feeds._clock = real_clock

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.py")).read()
body = src[src.index("    def _live_analysis(self):"):src.index("    def _ai_prices(self):")]
check("_live_analysis uses the shared computation, as itself", "_shared_analysis(name, df, owner=id(self))" in body
      and "compute_technical_signal(df" not in body and "compute_market_trend(df" not in body)

print()
print("SHARED ANALYSIS TEST PASSED" if not fails else f"SHARED ANALYSIS TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
