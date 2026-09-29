"""candle_pattern_study.candle_patterns() against hand-built textbook shapes.

Each bar's OHLC is chosen to be an unambiguous example (or non-example) of one pattern, so a wrong
threshold or a flipped comparison shows up as a specific named failure, not just "some bar disagreed"."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candle_pattern_study as cps

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def bars(rows):
    """rows: list of (open, high, low, close). Returns a DataFrame candle_patterns() can read."""
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])


print("1. HAMMER — small body near the top, long lower shadow, little/no upper shadow")
df = bars([
    (100, 101, 90, 100.5),     # a hammer: body 0.5, lower shadow 9.5 (>=2x), upper shadow 0 (<=body)
    (100, 105, 99, 101),       # an ordinary bullish bar - not a hammer
])
bullish, bearish, doji = cps.candle_patterns(df)
check("bar 0 is read as bullish (hammer)", bool(bullish[0]))
check("bar 0 is not read as bearish", not bearish[0])
check("bar 1 (ordinary bar) is not a hammer", not bullish[1])

print("2. SHOOTING STAR — small body near the bottom, long upper shadow, little/no lower shadow")
df = bars([
    (100, 110, 99.5, 100.5),   # shooting star: body 0.5, upper shadow 9.5, lower shadow 0.5(<=body)
    (100, 101, 95, 99),        # an ordinary bearish bar - not a shooting star
])
bullish, bearish, doji = cps.candle_patterns(df)
check("bar 0 is read as bearish (shooting star)", bool(bearish[0]))
check("bar 0 is not read as bullish", not bullish[0])
check("bar 1 (ordinary bar) is not a shooting star", not bearish[1])

print("3. BULLISH ENGULFING — a bearish bar followed by a bigger bullish bar swallowing its body")
df = bars([
    (100, 101, 95, 96),        # bearish bar: body 95->? open 100 close 96
    (95, 102, 94.5, 101),      # bullish bar opening at/below prev close, closing at/above prev open
])
bullish, bearish, doji = cps.candle_patterns(df)
check("bar 1 is read as bullish (engulfing)", bool(bullish[1]))
check("the first bar in the series is never flagged (nothing precedes it)", not bullish[0] and not bearish[0])

print("4. BEARISH ENGULFING — a bullish bar followed by a bigger bearish bar swallowing its body")
df = bars([
    (95, 101, 94, 100),        # bullish bar: open 95 close 100
    (101, 101.5, 93, 94),      # bearish bar opening at/above prev close, closing at/below prev open
])
bullish, bearish, doji = cps.candle_patterns(df)
check("bar 1 is read as bearish (engulfing)", bool(bearish[1]))

print("4b. THE RIGHT COLOURS IN SEQUENCE IS NOT ENOUGH - THE BODY MUST ACTUALLY ENGULF")
df = bars([
    (100, 101, 90, 91),        # a big bearish bar: body from 100 down to 91
    (95, 96, 94, 96),          # a small bullish bar entirely INSIDE the bar above - same colour
    (100, 108, 99, 107),       # ...   # sequence as an engulfing pair, but it swallows nothing
])
bullish, bearish, doji = cps.candle_patterns(df)
check("a same-coloured-sequence bar that does not contain the prior body is NOT bullish engulfing",
      not bullish[1])
df2 = bars([
    (95, 101, 94, 100),        # a bullish bar: body from 95 up to 100
    (96, 97, 94.5, 95),        # a small bearish bar entirely INSIDE the bar above
])
bullish2, bearish2, doji2 = cps.candle_patterns(df2)
check("...and the same the other way round for bearish engulfing", not bearish2[1])

print("5. AN ORDINARY TRENDING BAR IS NEITHER SHAPE, NEITHER DIRECTION FALSELY FIRES")
df = bars([
    (100, 108, 99, 107),       # a strong bullish bar with only a small wick either side
])
bullish, bearish, doji = cps.candle_patterns(df)
check("not read as bearish", not bearish[0])
check("small-wick trending bars are not forced into hammer/engulfing either", not bullish[0])

print("6. DOJI — open and close almost equal, regardless of where the shadows sit")
df = bars([
    (100, 105, 95, 100.2),     # body 0.2 on a range of 10 -> well under the 10% threshold
    (100, 108, 98, 106),       # wide body (6 on a range of 10) -> not a doji
])
bullish, bearish, doji = cps.candle_patterns(df)
check("the small-body bar is a doji", bool(doji[0]))
check("the wide-body bar is not", not doji[1])

print("7. A DOJI CAN COEXIST WITH NEITHER OR BOTH SHAPE FLAGS DEPENDING ON SHADOWS - SANITY, NOT A VETO HERE")
# candle_patterns() itself does not apply the doji veto - that is pattern_gate()'s job, checked next.
check("candle_patterns() returns three same-length arrays", len(bullish) == len(bearish) == len(doji) == len(df))

print("8. pattern_gate() VETOES A DOJI EVEN IF THE SHAPE ARRAYS WOULD OTHERWISE ALLOW IT")
class _FakeRec(dict):
    pass
bullish = np.array([True])
bearish = np.array([False])
doji = np.array([True])
rec = {"option_type": "CE"}
orig_live_gate = cps.res.live_gate
cps.res.live_gate = lambda *a, **k: True   # entry gates pass; only the pattern/doji check is under test
try:
    ok = cps.pattern_gate("NIFTY", 0, rec, None, bullish, bearish, doji)
finally:
    cps.res.live_gate = orig_live_gate
check("a Doji bar is refused even though 'bullish' is True", not ok)

print("9. pattern_gate() MATCHES THE TRADE'S OWN DIRECTION, NOT JUST 'ANY PATTERN'")
bullish = np.array([True])
bearish = np.array([False])
doji = np.array([False])
cps.res.live_gate = lambda *a, **k: True
try:
    ce_ok = cps.pattern_gate("NIFTY", 0, {"option_type": "CE"}, None, bullish, bearish, doji)
    pe_ok = cps.pattern_gate("NIFTY", 0, {"option_type": "PE"}, None, bullish, bearish, doji)
finally:
    cps.res.live_gate = orig_live_gate
check("a bullish-only bar allows a CE entry", ce_ok)
check("...but not a PE entry", not pe_ok)

print()
print("CANDLE PATTERN STUDY TEST PASSED" if not fails else f"CANDLE PATTERN STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
