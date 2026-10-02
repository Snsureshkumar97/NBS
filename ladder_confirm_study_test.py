#!/usr/bin/env python3
"""ladder_confirm_study.simulate_ladder() - hand-traced, bar by bar.

The mechanic, as the user described it by hand: the first lot books the
instant T1 is TOUCHED. The stop for whatever remains does NOT move on that
touch - it stays exactly where it was until a CONFIRMING candle CLOSES
beyond T1 (not a touch - a bare touch is routinely retested), at which point
the stop ratchets to THAT CANDLE'S OWN LOW (not to T1 itself). The same pair
of steps repeats at T2, then T3. Past T3, the identical rule continues with
no further named target ("it goes on" - the file's own docstring explains
why this is a judgement call, not a transcript).

Fakes only - plain numpy arrays, no history fetch, no network, no Kite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import ladder_confirm_study as lc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def arr(hi, lo, cl=None, end=None):
    n = len(hi)
    if cl is None:
        cl = lo
    return {"hi": np.array(hi, dtype=float), "lo": np.array(lo, dtype=float),
            "cl": np.array(cl, dtype=float),
            "end": np.array(end if end is not None else [False] * n, dtype=bool)}


def trade(side="CE", entry=100.0, stop=90.0, t1=110.0, t2=120.0, t3=130.0):
    return {"side": side, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "t3": t3}


print("1. n_lots < 3 IS REFUSED - THERE MUST BE A LOT FOR EACH OF T1/T2/T3")
try:
    lc.simulate_ladder(arr(hi=[100], lo=[99]), -1, trade(), n_lots=2)
    check("raises ValueError for n_lots=2", False)
except ValueError:
    check("raises ValueError for n_lots=2", True)

print("2. NEVER REACHING T1: DEGRADES TO A PLAIN, SINGLE-LEG, FULL-SIZE TRADE")
A = arr(hi=[103, 104, 95], lo=[97, 98, 89], cl=[102, 103, 91])
legs = lc.simulate_ladder(A, -1, trade())
check("one leg, full weight, at the original stop - nothing ladder-specific happened",
      legs == [(90.0, 2, 1.0)], legs)

print("3. T1 TOUCHED, NO CONFIRMING CLOSE EVER HAPPENS: THE STOP NEVER MOVES AT ALL")
A = arr(hi=[112, 95], lo=[108, 89], cl=[109, 90])
legs = lc.simulate_ladder(A, -1, trade())
check("lot 1 books at T1 (0.25), the remaining 0.75 stops at the ORIGINAL stop (90) - "
      "not breakeven, because no close ever confirmed above T1 to move it",
      legs == [(110.0, 0, 0.25), (90.0, 1, 0.75)], legs)

print("4. T1 TOUCHED, THEN CONFIRMED NEXT BAR: THE STOP RATCHETS TO THAT CANDLE'S OWN LOW")
A = arr(hi=[112, 115, 112], lo=[108, 111, 105], cl=[109, 113, 108])
legs = lc.simulate_ladder(A, -1, trade())
check("lot 1 at T1 (0.25); the confirming bar's own low (111) becomes the new stop, "
      "which is what the pullback in bar 2 actually hits - not the original 90",
      legs == [(110.0, 0, 0.25), (111.0, 2, 0.75)], legs)

print("5. ONE TOUCH PER BAR: A SINGLE BAR THAT CLEARS BOTH T1 AND T2 STILL ONLY BOOKS T1 THERE")
A = arr(hi=[122, 125], lo=[118, 121], cl=[121, 123])
legs = lc.simulate_ladder(A, -1, trade())
check("bar 0: T1 touched AND confirmed in the same bar (its own close already clears T1) - "
      "but T2 is NOT also booked there, even though this bar's high clears it too",
      legs[0] == (110.0, 0, 0.25), legs)
check("T2 only gets touched (and confirmed, same bar) in bar 1, not bar 0",
      legs[1] == (120.0, 1, 0.25), legs)
check("the remaining half, untouched by any named target, closes at the data's own end "
      "(bar 1's close, 123) since the array runs out there",
      legs[2] == (123.0, 1, 0.5), legs)

print("6. THE FULL STAIRCASE: T1 -> T2 -> T3, EACH TOUCHED THEN LATER CONFIRMED, "
      "THEN THE RUNNER LOT EXTENDS PAST T3 AND IS CAUGHT BY ITS OWN TRAIL")
A = arr(hi=[112, 113, 122, 123, 132, 135, 145, 142],
        lo=[108, 109, 118, 119, 128, 130, 140, 136],
        cl=[109, 111, 119, 121, 129, 133, 144, 138])
legs = lc.simulate_ladder(A, -1, trade())
check("lot 1 at T1 (bar 0, touch only - no confirm that bar since its close stays under T1)",
      legs[0] == (110.0, 0, 0.25), legs)
check("lot 2 at T2 (bar 2, touch only)", legs[1] == (120.0, 2, 0.25), legs)
check("lot 3 at T3 (bar 4, touch only)", legs[2] == (130.0, 4, 0.25), legs)
check("the final runner lot is caught by its own trailing stop at 140 (bar 7) - "
      "clear of T3 (130), the entire point of the mechanic",
      legs[3] == (140.0, 7, 0.25), legs)
check("the four legs' weights sum to exactly one full position",
      abs(sum(w for _, _, w in legs) - 1.0) < 1e-9, legs)

print("7. THE STOP NEVER RATCHETS BACKWARDS, EVEN DURING THE UNBOUNDED 'IT GOES ON' PHASE")
# Same staircase as #6 up to T3 (stop ratchets to 130, best=133 after bar 5),
# then a bar that dips in CLOSE (132 < best 133, so no new ratchet) but whose
# LOW (131) stays safely above the current stop (130) - must change nothing.
# Then a fresh high ratchets the trail further, then the real stop-out.
A = arr(hi=[112, 113, 122, 123, 132, 135, 135, 148, 136],
        lo=[108, 109, 118, 119, 128, 130, 131, 143, 132],
        cl=[109, 111, 119, 121, 129, 133, 132, 147, 134])
legs = lc.simulate_ladder(A, -1, trade())
check("the dip bar (6) neither stops the runner out nor shows up as any leg - "
      "its low (131) stays above the current stop (130), and its close (132) "
      "is below the current best (133) so nothing ratchets either way",
      all(j != 6 for _, j, _ in legs), legs)
check("a fresh high in bar 7 (close 147) ratchets the trail further, to that bar's own low (143)",
      legs[-1] == (143.0, 8, 0.25), legs)

print("8. PE MIRROR: EVERYTHING INVERTS - LOWER TARGETS, HIGHER STOPS, TRAIL ABOVE THE BEST LOW")
pe = trade(side="PE", entry=100, stop=110, t1=90, t2=80, t3=70)
A = arr(hi=[94, 91, 82, 79, 72, 68, 58, 64],
        lo=[88, 87, 78, 77, 68, 65, 55, 60],
        cl=[91, 89, 79, 77, 69, 65, 56, 63])
legs = lc.simulate_ladder(A, -1, pe)
check("lot 1 at T1 (90), lot 2 at T2 (80), lot 3 at T3 (70) - all touches, mirrored down",
      legs[0] == (90.0, 0, 0.25) and legs[1] == (80.0, 2, 0.25) and legs[2] == (70.0, 4, 0.25),
      legs)
check("the runner lot is caught below T3 - more favourable for a PE, mirroring case 6 exactly",
      legs[3][0] < 70.0, legs)

print("9. square_off: CLOSES AT THE DAY'S LAST CANDLE EVEN MID-LADDER, NOTHING ELSE TOUCHED YET")
A = arr(hi=[112, 103], lo=[108, 99], cl=[109, 102], end=[False, True])
legs = lc.simulate_ladder(A, -1, trade(), square_off=True)
check("lot 1 booked at T1, the remaining 0.75 closes at bar 1's own close (flagged end-of-day)",
      legs == [(110.0, 0, 0.25), (102.0, 1, 0.75)], legs)

print("10. hold_bars: THE RUNNER FALLS BACK TO THE LAST BAR IT WAS GIVEN WHEN NOTHING ELSE FIRES")
A = arr(hi=[112, 113, 114, 115], lo=[108, 109, 110, 111], cl=[109, 111, 112, 113])
legs = lc.simulate_ladder(A, -1, trade(), hold_bars=3, square_off=False)
check("hold_bars=3 only processes bars 0-2 (never bar 3) - the remainder (0.75, since only "
      "T1 got touched+confirmed by then) closes at bar 2's own close, the last one seen",
      legs[-1] == (112.0, 2, 0.75) and len(legs) == 2, legs)

print("11. A DIFFERENT n_lots SCALES THE WEIGHTS CORRECTLY (5 lots: 0.2 each)")
A = arr(hi=[112, 113, 122, 123, 132, 135, 145, 142],
        lo=[108, 109, 118, 119, 128, 130, 140, 136],
        cl=[109, 111, 119, 121, 129, 133, 144, 138])
legs = lc.simulate_ladder(A, -1, trade(), n_lots=5)
check("the same staircase as #6, but each booked leg now carries weight 1/5, not 1/4",
      all(abs(w - 0.2) < 1e-9 for _, _, w in legs[:3]), legs)
check("the final runner lot (2/5 of the position, since only 3 of 5 lots have booked) "
      "is still caught at the same 140 the 4-lot version found",
      legs[-1][:2] == (140.0, 7) and abs(legs[-1][2] - 0.4) < 1e-9, legs)

print("12. CONFIRMATION ALWAYS TARGETS THE EARLIEST PENDING RUNG, NOT WHATEVER WAS LAST TOUCHED - "
      "touch gets two rungs ahead of confirmation (T1 then T2, neither ever confirmed yet) "
      "before any candle actually closes, and that close must be checked against T1, not T2")
A = arr(hi=[112, 122, 113, 110], lo=[108, 118, 109, 105], cl=[105, 108, 111, 107])
legs = lc.simulate_ladder(A, -1, trade())
check("lot 1 at T1 (bar 0), lot 2 at T2 (bar 1) - touched is now 2 rungs ahead of confirmed (still 0)",
      legs[0] == (110.0, 0, 0.25) and legs[1] == (120.0, 1, 0.25), legs)
check("bar 2's close (111) confirms T1 - the EARLIEST pending rung - even though T2 was touched "
      "more recently; the stop ratchets to bar 2's own low (109), which is what bar 3's dip to "
      "105 then hits - a wrong implementation checking against T2 (120) instead would never "
      "confirm here at all, leaving the original stop (90) in place and bar 3 non-eventful",
      legs[2] == (109.0, 3, 0.5), legs)

print("LADDER CONFIRM STUDY TEST PASSED" if not fails else f"LADDER CONFIRM STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
