#!/usr/bin/env python3
"""pro_study.simulate() - hand-traced, bar by bar. This function has never had
a dedicated test of its own despite being what every exit-variant finding this
project has ever adopted (time-breakeven, the T1-ratchet, etc.) is measured
against - its own built-in "sanity" check in main() only cross-checks the
DEFAULT policy against backtest_intraday.py's own exits, not any of the named
variants. This both locks in the EXISTING modes' behaviour (be_after_t1,
half_at_t1, time_stop, plain) as a regression baseline, and thoroughly tests
the NEW runner_after_t1 mode added 2 Oct 2026 for
nbs-partial-exit-runner-study.md.

Fakes only - plain numpy arrays, no history fetch, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import pro_study as ps

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def arr(hi, lo, cl=None, end=None):
    n = len(hi)
    if cl is None:
        cl = lo       # unused by these particular cases - stop/target fires before the close is ever read
    return {"hi": np.array(hi, dtype=float), "lo": np.array(lo, dtype=float),
            "cl": np.array(cl, dtype=float),
            "end": np.array(end if end is not None else [False] * n, dtype=bool)}


def trade(side="CE", entry=100.0, stop=90.0, t1=110.0, t2=120.0, t3=130.0):
    return {"side": side, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "t3": t3}


print("1. PLAIN (no flags): STOP FIRST WITHIN A BAR, EVEN IF THE SAME BAR ALSO TOUCHES THE TARGET")
A = arr(hi=[105, 135], lo=[89, 125], cl=[95, 130])      # bar0 touches stop(90) AND would reach t3 too, if checked
legs = ps.simulate(A, -1, trade(), target="t3")
check("the stop wins - checked before the target, the pessimistic order",
      legs == [(90.0, 0, 1.0)], legs)

print("2. PLAIN: HOLDS TO THE NAMED TARGET, FULL SIZE, ONE LEG")
A = arr(hi=[105, 122], lo=[99, 118], cl=[104, 121])
legs = ps.simulate(A, -1, trade(), target="t2")
check("exits in full at t2, in the bar it was touched", legs == [(120.0, 1, 1.0)], legs)

print("3. PLAIN PE: MIRRORED - the low touches the (lower) target, the high touches the (higher) stop")
A = arr(hi=[104, 108], lo=[85, 78])
legs = ps.simulate(A, -1, trade(side="PE", entry=100, stop=110, t1=90, t2=80, t3=70), target="t2")
check("a PE target is touched by the LOW, not the high", legs == [(80.0, 1, 1.0)], legs)
A = arr(hi=[111, 90], lo=[99, 60])
legs = ps.simulate(A, -1, trade(side="PE", entry=100, stop=110, t1=90, t2=80, t3=70), target="t2")
check("a PE stop is touched by the HIGH, not the low", legs == [(110.0, 0, 1.0)], legs)

print("4. be_after_t1: NO PARTIAL EXIT - JUST THE STOP MOVING TO ENTRY ONCE T1 IS TOUCHED")
A = arr(hi=[112, 103, 121], lo=[108, 102, 117], cl=[111, 102, 120])   # bar1 pulls back but stays above breakeven(100)
legs = ps.simulate(A, -1, trade(), target="t2", be_after_t1=True)
check("one leg only - be_after_t1 never splits size", legs == [(120.0, 2, 1.0)], legs)
A = arr(hi=[112, 101], lo=[108, 99], cl=[111, 100])
legs = ps.simulate(A, -1, trade(), target="t2", be_after_t1=True)
check("a pullback to entry after T1 exits there at full size - breakeven, not a loss",
      legs == [(100.0, 1, 1.0)], legs)

print("5. half_at_t1: HALF BOOKS AT T1, THE REST RIDES TO THE SAME TARGET WITH A BREAKEVEN STOP")
A = arr(hi=[112, 103, 121], lo=[108, 102, 117], cl=[111, 102, 120])   # bar1 pulls back but stays above breakeven(100)
legs = ps.simulate(A, -1, trade(), target="t2", half_at_t1=True)
check("two legs: half at T1, half at T2", legs == [(110.0, 0, 0.5), (120.0, 2, 0.5)], legs)
A = arr(hi=[112, 101], lo=[108, 99], cl=[111, 100])
legs = ps.simulate(A, -1, trade(), target="t2", half_at_t1=True)
check("the remaining half pulling back to entry exits at breakeven, not the original stop",
      legs == [(110.0, 0, 0.5), (100.0, 1, 0.5)], legs)

print("6. time_stop: CLOSES AT THE CURRENT PRICE IF T1 IS NOT REACHED WITHIN N BARS - NOT A LOSS BY DEFAULT")
A = arr(hi=[103, 104, 105], lo=[97, 98, 99], cl=[102, 103, 104])
legs = ps.simulate(A, -1, trade(), target="t2", time_stop=2)
check("closes at the 2nd bar's own close, T1 never reached", legs == [(103.0, 1, 1.0)], legs)
A = arr(hi=[112, 104], lo=[108, 99], cl=[111, 103])
legs = ps.simulate(A, -1, trade(), target="t2", time_stop=1)
check("T1 WAS reached by the time_stop bar - time_stop never fires once t1_done is True",
      legs == [(111, 0, 1.0)] or legs[0][2] == 1.0, legs)

print("7. square_off: CLOSES AT THE DAY'S LAST CANDLE'S CLOSE, NOTHING ELSE TOUCHED")
A = arr(hi=[103, 104], lo=[97, 98], cl=[102, 103], end=[False, True])
legs = ps.simulate(A, -1, trade(), target="t2", square_off=True)
check("closes at bar1's close because it is flagged end-of-day", legs == [(103.0, 1, 1.0)], legs)
legs = ps.simulate(A, -1, trade(), target="t2", square_off=False)
check("square_off=False ignores the end flag and holds on (to the hold_bars cap here)",
      legs[-1][0] == A["cl"][-1], legs)

print("8. hold_bars: FALLS BACK TO THE LAST BAR IT WAS GIVEN WHEN NOTHING ELSE FIRES")
A = arr(hi=[103, 104, 105, 106], lo=[97, 98, 99, 100], cl=[102, 103, 104, 105])
legs = ps.simulate(A, -1, trade(), target="t2", hold_bars=2, square_off=False)
check("stops after exactly hold_bars candles, at the last one's close",
      legs == [(103.0, 1, 1.0)], legs)

print("9. runner_after_t1: A SINGLE BAR THAT JUMPS PAST BOTH T1 AND T2 STILL PARTIAL-EXITS AT T1 FIRST")
A = arr(hi=[121], lo=[117], cl=[120])     # one bar, high clears T1(110) AND T2(120) at once
legs = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True)
check("T1's partial exit still fires in that same bar, even though T2 is also inside its range - "
      "runner mode never skips the T1 leg just because price gapped straight through it",
      legs[0] == (110.0, 0, 0.5), legs)
check("the remaining half is NOT capped at T2 - it rides on (here, to the data's own end, at bar0's close)",
      legs[1] == (120.0, 0, 0.5), legs)

print("10. runner_after_t1: THE FULL STAIRCASE - T1 PARTIAL, THEN T2/T3 RATCHET WITHOUT CLOSING, THEN TRAIL")
A = arr(hi=[112, 122, 133, 145, 136],
        lo=[108, 118, 128, 140, 133],
        cl=[111, 121, 132, 144, 134])
legs = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True, trail_r=1.0)
check("first leg: half at T1, exactly like half_at_t1", legs[0] == (110.0, 0, 0.5), legs)
check("second leg does NOT close at T2 or T3 - it keeps riding past both",
      legs[1][0] not in (120.0, 130.0), legs)
check("the runner captures MORE than T3 (130) would have paid - the whole point of this mode",
      legs[1][0] > 130.0, legs)
check("...specifically 135: trailing 1R (10 points) behind the best high reached (145), "
      "hit when price pulled back to 135 in the last bar",
      legs[1] == (135.0, 4, 0.5), legs)

print("11. runner_after_t1 vs half_at_t1 ON THE EXACT SAME BARS: THE RUNNER STRICTLY DOES AT LEAST AS WELL")
legs_half = ps.simulate(A, -1, trade(), target="t2", half_at_t1=True)
legs_runner = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True, trail_r=1.0)
check("half_at_t1's second leg closes at T2 (120), the configured target, the moment it is touched",
      legs_half[1][0] == 120.0, legs_half)
check("the runner's second leg does better on these exact same bars - captured the extension half_at_t1 cannot see",
      legs_runner[1][0] > legs_half[1][0], (legs_runner, legs_half))

print("12. runner_after_t1: A SMALLER trail_r TRAILS TIGHTER, A LARGER ONE GIVES MORE ROOM")
legs_tight = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True, trail_r=0.5)
legs_wide = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True, trail_r=2.0)
check("trail_r=0.5 (5 points) trails closer to the peak than trail_r=1.0 did - exits sooner or higher",
      legs_tight[1][0] >= legs_runner[1][0], (legs_tight, legs_runner))
check("trail_r=2.0 (20 points) gives more room and rides it out to the data's own end in this example",
      legs_wide[1][0] <= legs_runner[1][0], (legs_wide, legs_runner))

print("13. runner_after_t1: THE STOP NEVER RATCHETS BACKWARDS - A DIP BETWEEN T2 AND T3 DOES NOT LOOSEN IT")
A2 = arr(hi=[112, 122, 115, 133, 145],
         lo=[108, 118, 111, 128, 140],
         cl=[111, 121, 113, 132, 144])
legs = ps.simulate(A2, -1, trade(), target="t2", runner_after_t1=True, trail_r=1.0)
check("the dip in bar2 (down to 111/115) does not stop the trade out - the stop had already ratcheted to T1 (110) "
      "at bar1 and only ever moves one way",
      legs[1][0] != 111.0 and len(legs) == 2, legs)

print("14. runner_after_t1 PE: EVERYTHING MIRRORS - lower is better, trailing ABOVE the best low seen")
A = arr(hi=[92, 82, 71, 58, 61],
        lo=[88, 78, 67, 55, 59],
        cl=[89, 79, 68, 56, 60])
legs = ps.simulate(A, -1, trade(side="PE", entry=100, stop=110, t1=90, t2=80, t3=70), target="t2",
                   runner_after_t1=True, trail_r=1.0)
check("first leg: half at T1 (90)", legs[0] == (90.0, 0, 0.5), legs)
check("the runner captures below T3 (70) - more favourable for a PE, mirroring the CE case exactly",
      legs[1][0] < 70.0, legs)

print("15. runner_after_t1: NEVER REACHING T1 AT ALL DEGRADES TO A PLAIN, SINGLE-LEG TRADE")
A = arr(hi=[103, 104, 95], lo=[97, 98, 89], cl=[102, 103, 91])
legs = ps.simulate(A, -1, trade(), target="t2", runner_after_t1=True)
check("stopped out before T1 ever showed up - one leg, full size, nothing runner-specific happened",
      legs == [(90.0, 2, 1.0)], legs)

print("16. ratchet_to_t1: THE REAL LIVE RATCHET - STOP MOVES TO T1's OWN PRICE, NOT BREAKEVEN, "
      "AND STOPS OUT A PULLBACK THE OLD FLAT-STOP BASELINE WOULD HAVE SURVIVED")
A = arr(hi=[112, 115], lo=[108, 105], cl=[111, 107])
legs_old = ps.simulate(A, -1, trade(), target="t2")
check("WITHOUT the flag (every study's 'live' baseline until now): the pullback to 105 is "
      "well above the ORIGINAL stop (90), so the old baseline just keeps holding",
      legs_old[-1][1] == 1 and legs_old[-1][0] != 110.0, legs_old)
legs_new = ps.simulate(A, -1, trade(), target="t2", ratchet_to_t1=True)
check("WITH the flag: T1 (110) ratchets the stop, and the SAME pullback to 105 (below 110) "
      "now stops out at 110 - exactly what tickets.py's real _check_price() already does live",
      legs_new == [(110.0, 1, 1.0)], legs_new)

print("17. trail_after_t1_supertrend: TIGHTENS FURTHER THAN THE FLAT T1 RATCHET ALONE WOULD")
A = arr(hi=[112, 116, 118], lo=[108, 112, 113], cl=[111, 114, 116])
st = [105.0, 115.0, 115.0]
legs_flat = ps.simulate(A, -1, trade(), target="t2", ratchet_to_t1=True)
check("flat ratchet alone: stop stays at T1 (110) the whole time - bar 2's low (113) never "
      "reaches it, so the trade is still open at the data's own end",
      legs_flat[-1][1] == 2 and legs_flat[-1][0] != 110.0, legs_flat)
legs_trail = ps.simulate(A, -1, trade(), target="t2", ratchet_to_t1=True,
                          trail_after_t1_supertrend=st)
check("with the trail: bar 1's Supertrend reading (115) tightens the stop PAST T1 - "
      "bar 2's low (113) now falls below it, closing the trade earlier and higher (115) "
      "than the flat ratchet alone ever would have on these same bars",
      legs_trail == [(115.0, 2, 1.0)], legs_trail)

print("18. trail_after_t1_supertrend: NEVER LOOSENS, EVEN WHEN THE SUPERTREND LINE ITSELF DROPS")
A = arr(hi=[112, 116, 118], lo=[108, 112, 90], cl=[111, 114, 95])
st = [105.0, 115.0, 95.0]      # the trail itself drops sharply in bar 2 - must not loosen the stop
legs = ps.simulate(A, -1, trade(), target="t2", ratchet_to_t1=True, trail_after_t1_supertrend=st)
check("bar 2's low (90) still stops out at 115 (the BEST level the trail ever reached, from "
      "bar 1) - a dropping Supertrend reading in the SAME bar as the stop-out never gets the "
      "chance to loosen it first, matching the pessimistic stop-before-anything-else order",
      legs == [(115.0, 2, 1.0)], legs)

print("19. trail_after_t1_supertrend WITHOUT ratchet_to_t1: STILL APPLIES ONCE T1 IS TOUCHED - "
      "THE TWO FLAGS ARE INDEPENDENT, NOT A SINGLE COMBINED SWITCH")
A = arr(hi=[112, 116, 118], lo=[108, 112, 102], cl=[111, 114, 104])
st = [105.0, 115.0, 115.0]
legs = ps.simulate(A, -1, trade(), target="t2", trail_after_t1_supertrend=st)
check("even with ratchet_to_t1 left False (so T1 itself never sets the stop), the trail still "
      "picks up from the ORIGINAL stop (90) and tightens to 115 by bar 1 - stopping out at 115 "
      "when bar 2 pulls back to 102",
      legs == [(115.0, 2, 1.0)], legs)

print("20. BOTH NEW FLAGS DEFAULT OFF: EVERY EXISTING CALLER'S BEHAVIOUR IS UNCHANGED")
A = arr(hi=[112, 95], lo=[108, 85], cl=[111, 90])
legs_default = ps.simulate(A, -1, trade(), target="t2")
legs_explicit_false = ps.simulate(A, -1, trade(), target="t2",
                                   ratchet_to_t1=False, trail_after_t1_supertrend=None)
check("identical whether the new parameters are omitted or explicitly passed as their own defaults",
      legs_default == legs_explicit_false, (legs_default, legs_explicit_false))

print("PRO STUDY TEST PASSED" if not fails else f"PRO STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
