#!/usr/bin/env python3
"""conditional_cooldown_study.sequential_trades_conditional() - hand-traced.

The rule: a same-direction follow-up's cooldown is WAIVED (0 min) only when
the prior trade of that direction closed by hitting its target AND ADX was
still at/above the trend threshold at that close - full cooldown otherwise
(a stop-out, a time/square-off exit, or a target hit in an already-fading
market). Exclusivity (one open position per index) always applies
regardless of any waiver.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import conditional_cooldown_study as cc

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def ts(s):
    return pd.Timestamp(f"2026-01-01 {s}")


def c(when, exit_when, side, closed_via="target", exit_adx=25.0):
    return {"when": ts(when), "exit_time": ts(exit_when), "side": side,
            "closed_via": closed_via, "exit_adx": exit_adx}


THRESH = 20.0   # matches config.ADX_TREND_THRESHOLD, passed explicitly in every call below

print("1. TARGET HIT WITH ADX AT/ABOVE THE THRESHOLD: THE COOLDOWN IS WAIVED ENTIRELY")
cands = [c("10:00", "10:30", "CE", closed_via="target", exit_adx=25.0),
         c("10:31", "10:35", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("both survive - only 1 minute after close, but the prior close earned a waiver",
      len(kept) == 2, kept)

print("2. TARGET HIT, BUT ADX HAD ALREADY FADED BELOW THE THRESHOLD: NO WAIVER, FULL COOLDOWN")
cands = [c("10:00", "10:30", "CE", closed_via="target", exit_adx=15.0),
         c("10:31", "10:35", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("only the first survives - target hit, but ADX(15) was already under the threshold(20)",
      len(kept) == 1, kept)

print("3. A STOP-OUT NEVER EARNS A WAIVER, NO MATTER HOW HIGH ADX WAS AT THAT MOMENT")
cands = [c("10:00", "10:30", "CE", closed_via="stop", exit_adx=35.0),
         c("10:31", "10:35", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("only the first survives - a stop-out gets the full cooldown regardless of ADX",
      len(kept) == 1, kept)

print("4. A TIME/SQUARE-OFF EXIT ('other') IS TREATED LIKE A STOP FOR THIS PURPOSE - NO WAIVER")
cands = [c("10:00", "10:30", "CE", closed_via="other", exit_adx=35.0),
         c("10:31", "10:35", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("only the first survives - never having reached target earns no waiver either",
      len(kept) == 1, kept)

print("5. A WAIVED COOLDOWN STILL RESPECTS EXCLUSIVITY - CANNOT OVERLAP A STILL-OPEN POSITION")
cands = [c("10:00", "11:00", "CE", closed_via="target", exit_adx=30.0),
         c("10:30", "11:30", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("only the first survives - it arrives at 10:30, before the first even closes at 11:00",
      len(kept) == 1, kept)

print("6. OPPOSITE DIRECTION IS NEVER SUBJECT TO ANY OF THIS - WAIVER RULES ONLY GOVERN "
      "A SAME-DIRECTION FOLLOW-UP")
cands = [c("10:00", "10:30", "CE", closed_via="stop", exit_adx=5.0),
         c("10:31", "11:00", "PE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("both survive - a CE stop-out (no waiver) has no bearing on a PE entry", len(kept) == 2, kept)

print("7. THE WAIVER BOUNDARY: ADX EXACTLY AT THE THRESHOLD STILL COUNTS AS A WAIVER")
cands = [c("10:00", "10:30", "CE", closed_via="target", exit_adx=20.0),
         c("10:30", "11:00", "CE")]
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("both survive - ADX(20) == the threshold(20), >= counts as still trending",
      len(kept) == 2, kept)

print("8. A BLOCKED CANDIDATE NEVER BECOMES THE NEW 'LAST TRADE' - THE NEXT CHECK IS STILL "
      "AGAINST WHAT ACTUALLY SURVIVED")
cands = [c("10:00", "10:30", "CE", closed_via="target", exit_adx=15.0),   # kept, no waiver (low ADX)
         c("10:35", "11:05", "CE", closed_via="target", exit_adx=30.0),  # blocked (inside cooldown)
         c("10:55", "11:25", "CE")]                                      # 25 min since 10:30 - clears it
kept = cc.sequential_trades_conditional(cands, base_cooldown_min=20, waive_adx_threshold=THRESH)
check("the middle candidate (which WOULD have earned a waiver) never gets the chance to - it "
      "is blocked before it can close, so the third is judged against the first trade's own "
      "(no-waiver) close, not the skipped one's",
      [tr["when"] for tr in kept] == [ts("10:00"), ts("10:55")], kept)

print("CONDITIONAL COOLDOWN STUDY TEST PASSED" if not fails else f"CONDITIONAL COOLDOWN STUDY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
