"""simulate_time_breakeven() against hand-built bar sequences - the price/stop/target machinery
mirrors pro_study.simulate()'s (already trusted, unchanged here), so this only exercises the ONE
new thing: the time-based breakeven tighten."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time_breakeven_study as tbs

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def bars(n, hi, lo, cl):
    """A: n bars of constant hi/lo/cl (per-bar overridden by the caller), never a session end,
    long enough for hold_bars=26 to never run out on its own."""
    return {"hi": np.array(hi, dtype=float), "lo": np.array(lo, dtype=float),
            "cl": np.array(cl, dtype=float), "end": np.zeros(len(hi), dtype=bool)}


def tr(side, entry, stop, t1, t2, t3):
    return {"side": side, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "t3": t3}


print("1. BEFORE wait_bars, BEHAVES EXACTLY LIKE THE ORDINARY STOP - NO EARLY TIGHTENING")
# CE: entry 100, stop 95, t1 110, t2 120. Price drifts down and touches the ORIGINAL stop (95)
# on bar 3 - well before wait_bars (8, the default). If the stop had tightened early, this would
# exit at 100 (breakeven) instead of 95.
i = 0
hi = [0, 101, 100, 99, 96]
lo = [0, 99, 98, 95, 94]
cl = [0, 100, 99, 96, 95]
A = bars(5, hi, lo, cl)
t = tr("CE", 100, 95, 110, 120, 130)
legs = tbs.simulate_time_breakeven(A, i, t, hold_bars=20)
check("exits at the ORIGINAL stop (95), not breakeven, since wait_bars has not elapsed",
      legs[0][0] == 95, legs)
check("...on bar 3, before any tightening could apply", legs[0][1] == 3, legs)

print("2. AFTER wait_bars WITH NO T1, THE STOP TIGHTENS TO BREAKEVEN")
# CE: entry 100, stop 95, t1 110 (never touched). Flat comfortably ABOVE breakeven (100.5) for
# 8 bars - so the tighten at bar 8 does not itself trigger the new stop - then drops through 100
# on bar 9: the time-breakeven should have already moved the stop up to 100 by then, so it exits
# at 100, not the original 95.
n = 12
hi = [0] + [101.5] * (n - 1)
lo = [0] + [100.5] * (n - 1)
cl = [0] + [101.0] * (n - 1)
lo[9], cl[9] = 90, 91          # bar 9 (i+9, 1 bar past wait_bars=8) drops through breakeven
A = bars(n, hi, lo, cl)
t = tr("CE", 100, 95, 110, 120, 130)
legs = tbs.simulate_time_breakeven(A, i, t, hold_bars=20, wait_bars=8)
check("exits at breakeven (100), the tightened stop, not the original 95", legs[0][0] == 100, legs)
check("...specifically on bar 9, not bar 8 - the tighten alone does not trigger it", legs[0][1] == 9, legs)

print("3. ONCE T1 IS TOUCHED, THE TIME MECHANISM NEVER FIRES (T1-TOUCHED TRADES ARE UNCHANGED)")
# CE: entry 100, stop 95, t1 105. T1 touches on bar 3 (well before wait_bars). Then price falls
# all the way to the ORIGINAL stop on bar 15 (well after wait_bars would have elapsed if it still
# applied). Since T1 already touched, the time-breakeven must NOT have moved the stop - it should
# still exit at the original 95, exactly as pro_study.simulate() would.
n = 20
hi = [0] + [101] * (n - 1)
lo = [0] + [99] * (n - 1)
cl = [0] + [100] * (n - 1)
hi[3] = 106       # T1 (105) touched here
lo[15], hi[15] = 94, 96   # original stop (95) touched well after wait_bars=8 would have elapsed
A = bars(n, hi, lo, cl)
t = tr("CE", 100, 95, 105, 120, 130)
legs = tbs.simulate_time_breakeven(A, i, t, hold_bars=20, wait_bars=8)
check("T1 was reached, so the original stop (95) still applies - not breakeven",
      legs[0][0] == 95, legs)

print("4. THE SAME MECHANIC, MIRRORED FOR A PE (stop tightens DOWN toward entry, not up)")
n = 12
hi = [0] + [99.5] * (n - 1)    # flat, comfortably BELOW breakeven so the tighten alone is quiet
lo = [0] + [98.5] * (n - 1)
cl = [0] + [99.0] * (n - 1)
hi[9], cl[9] = 110, 109        # bar 9 rallies hard through breakeven, against a PE
A = bars(n, hi, lo, cl)
t = tr("PE", 100, 105, 90, 80, 70)    # PE: stop ABOVE entry
legs = tbs.simulate_time_breakeven(A, i, t, hold_bars=20, wait_bars=8)
check("a PE's stop tightens DOWN to breakeven (100), not the original 105",
      legs[0][0] == 100, legs)
check("...specifically on bar 9, not bar 8", legs[0][1] == 9, legs)

print("5. A WINNING TRADE (TARGET HIT BEFORE THE TIME LIMIT) IS COMPLETELY UNAFFECTED")
n = 12
hi = [0, 101, 108, 112, 121] + [121] * (n - 5)
lo = [0, 99, 100, 108, 119] + [119] * (n - 5)
cl = [0, 100, 107, 111, 120] + [120] * (n - 5)
A = bars(n, hi, lo, cl)
t = tr("CE", 100, 95, 108, 120, 130)
legs = tbs.simulate_time_breakeven(A, i, t, hold_bars=20, wait_bars=8, target="t2")
check("exits at the target (120) on bar 4, well before wait_bars, exactly like a normal trade",
      legs[0][0] == 120 and legs[0][1] == 4, legs)

print()
print("TIME BREAKEVEN STUDY TEST PASSED" if not fails else f"TIME BREAKEVEN STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
