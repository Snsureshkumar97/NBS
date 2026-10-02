#!/usr/bin/env python3
"""reentry_cooldown_study.sequential_trades() - hand-traced.

Models what the live tool actually enforces that every OTHER study in this
project's candidate lists ignore: at most one open position per index at a
time, and a same-direction follow-up only after its own cooldown elapses
since the LAST trade of that direction closed. A blocked candidate must
never update either piece of state - it never happened, so it cannot be
the "last" anything.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import reentry_cooldown_study as rc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def ts(s):
    return pd.Timestamp(f"2026-01-01 {s}")


def c(when, exit_when, side):
    return {"when": ts(when), "exit_time": ts(exit_when), "side": side}


print("1. TWO SAME-DIRECTION TRADES WITH A GAP AT LEAST AS LONG AS THE COOLDOWN: BOTH KEPT")
cands = [c("10:00", "10:30", "CE"), c("11:00", "11:30", "CE")]
kept = rc.sequential_trades(cands, cooldown_min=20)
check("both survive - 30 minutes since the first one closed clears a 20-minute cooldown",
      len(kept) == 2, kept)

print("2. THE SAME GAP, BUT INSIDE THE COOLDOWN: THE SECOND ONE IS NEVER ISSUED")
cands = [c("10:00", "10:30", "CE"), c("10:40", "11:00", "CE")]
kept = rc.sequential_trades(cands, cooldown_min=20)
check("only the first survives - only 10 minutes have passed, inside a 20-minute cooldown",
      len(kept) == 1 and kept[0]["when"] == ts("10:00"), kept)

print("3. AN OPPOSITE-DIRECTION ENTRY RIGHT AFTER A CLOSE IS NEVER SUBJECT TO THIS COOLDOWN")
cands = [c("10:00", "10:30", "CE"), c("10:31", "11:00", "PE")]
kept = rc.sequential_trades(cands, cooldown_min=20)
check("both survive - the cooldown only ever governs a SAME-direction follow-up",
      len(kept) == 2, kept)

print("4. OVERLAP (A POSITION STILL OPEN) BLOCKS A CANDIDATE REGARDLESS OF DIRECTION - "
      "only one open position per index at a time, cooldown value aside entirely")
cands = [c("10:00", "11:00", "CE"), c("10:30", "11:30", "PE")]
kept = rc.sequential_trades(cands, cooldown_min=0)
check("the second (opposite-direction!) candidate is still blocked - it arrives while the "
      "first is still open, even with cooldown_min=0",
      len(kept) == 1 and kept[0]["when"] == ts("10:00"), kept)

print("5. A BLOCKED CANDIDATE NEVER UPDATES STATE - IT NEVER HAPPENED, SO A LATER ONE "
      "IS JUDGED AGAINST THE LAST TRADE THAT ACTUALLY SURVIVED, NOT THE SKIPPED ONE")
cands = [c("10:00", "10:30", "CE"),       # kept
         c("10:35", "11:05", "CE"),       # blocked - only 5 min since 10:30, inside 20-min cooldown
         c("10:55", "11:25", "CE")]       # 25 min since 10:30 (the real last close) - clears it
kept = rc.sequential_trades(cands, cooldown_min=20)
check("the middle candidate is skipped and the third is judged against the FIRST trade's own "
      "close (10:30), not the skipped one - 25 minutes clears a 20-minute cooldown",
      [tr["when"] for tr in kept] == [ts("10:00"), ts("10:55")], kept)

print("6. cooldown_min=0 ALLOWS A SAME-DIRECTION RE-ENTRY THE INSTANT THE PRIOR ONE CLOSES")
cands = [c("10:00", "10:30", "CE"), c("10:30", "11:00", "CE")]
kept = rc.sequential_trades(cands, cooldown_min=0)
check("both survive - with no cooldown, only exclusivity (not being open at the same time) matters",
      len(kept) == 2, kept)

print("7. PER-DIRECTION INDEPENDENCE: AN INTERVENING OPPOSITE-DIRECTION TRADE DOES NOT RESET "
      "OR DISTURB THE ORIGINAL DIRECTION'S OWN COOLDOWN CLOCK")
cands = [c("10:00", "10:30", "CE"),        # kept, starts CE's clock at 10:30
         c("10:31", "10:35", "PE"),        # kept (opposite direction, no overlap)
         c("10:40", "11:00", "CE")]        # judged against 10:30 (CE's own last close), not 10:35
kept = rc.sequential_trades(cands, cooldown_min=20)
check("the third candidate (CE again) is still blocked at 10:40 - only 10 minutes since the "
      "FIRST CE trade's own close (10:30), unaffected by the PE trade sitting in between",
      [tr["when"] for tr in kept] == [ts("10:00"), ts("10:31")], kept)

print("REENTRY COOLDOWN STUDY TEST PASSED" if not fails else f"REENTRY COOLDOWN STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
