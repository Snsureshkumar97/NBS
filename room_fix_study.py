#!/usr/bin/env python3
"""
room_fix_study.py — which part of the live room to run costs the money, and does matching the tested room fix it?
================================================================================
The user, 14 Oct 2026, after live_room_study.py (the rules earn far less with the room live really uses): "yes run it".

The live room differs from the tested one in two ways - the option market's expected move caps it (the backtest has no
chain), and its "normal day" is the mean of the last ~10 days (the backtest: every prior day since 2023). FIXED BEFORE
ANY RESULT WAS SEEN (14 Oct 2026), the same signal_engine.compute_reachability with (live_room_study.install_live_room):
    A  no expected-move cap, every prior day      - the room every 3-year study used (asserted)
    B  the expected-move cap, 10 days              - LIVE TODAY (live_room_study's LIVE, asserted)
    C  NO expected-move cap, 10 days               - what the cap costs (B vs C); a fix live can run as it is
    D  the expected-move cap, every prior day      - what the short normal day costs (B vs D)
    E  NO expected-move cap, 60 days               - a fix live can run with its 60-day chart window
Through the rules as they run now (t3_exit_recheck_study's "t3re" 15-minute walk, all four indices; Sensex also the
5-minute walk with the step at the candle close). A FIX (C or E) is kept only if it makes more than B in BOTH the first
two years and the held-out year, Sensex + Midcap pooled; Sensex's 5-minute walk reported beside it. The OI walls stay
out of every variant (no history of them).

RESULT (14 Oct 2026, history to 11 Sep 2026; room_fix_study_results.txt). The first run was WRONG and is not used:
live_room_study.install_live_room's inner variable "chain" shadowed its own switch, so C and E still had the cap
(they equalled B to the rupee); fixed, checked on 400 Sensex candles (C: no expected move on any, median room 507
against B's 226), re-run.
THE EXPECTED-MOVE CAP IS WHAT COSTS - it binds on ~70% of candles and halves the room. Sensex + Midcap, 15-minute,
first two years / held-out:
  B  live today          +287,984 / +142,514
  C  NO cap, 10 days     +489,948 / +204,688   KEEP against B: +201,964 / +62,174 (worst drop +6,755 / -17,746)
  E  NO cap, 60 days     +394,621 / +169,399   KEEP against B: +106,637 / +26,885
  D  cap, every day      +222,684 / +161,240   (the normal day is not what costs)
  A  as tested           +370,798 / +229,174
Sensex 5-minute: B +13,713 / +19,727, C +199,733 / +63,091 (+186,021 / +43,364), E +131,017 / +78,705. By index C is
better in both periods on Sensex (+225,647 / +119,188 against +78,098 / +55,603); on Midcap better in the first two
years (+264,301 against +209,886) and level in the held-out one (+85,500 against +86,911). C is what live can run as
it is: the room from the day's range alone - no expected-move or OI-wall limit - on its own ~10-day normal day.
Not built: the user's call.

    python3 room_fix_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("SENSEX", "MIDCPNIFTY", "NIFTY", "BANKNIFTY")
RULES_LIVE = ("SENSEX", "MIDCPNIFTY")
MODES = {"A": (None, None), "B": (True, 10), "C": (False, 10), "D": (True, None), "E": (False, 60)}
LABEL = {"A": "A: no cap, every day (as tested)", "B": "B: cap, 10 days (LIVE TODAY)", "C": "C: NO cap, 10 days",
         "D": "D: cap, every day", "E": "E: NO cap, 60 days"}
LIVE_FIGS = {("SENSEX", "15"): (78098, 55603), ("MIDCPNIFTY", "15"): (209886, 86911), ("SENSEX", "5"): (13713, 19727)}
TESTED = {("SENSEX", "15"): (176460, 135052), ("MIDCPNIFTY", "15"): (194339, 94122), ("SENSEX", "5"): (122814, 108374)}


def _job(job):
    key, walk, mode = job
    import live_room_study as lrs
    chain, days = MODES[mode]
    if mode != "A":
        lrs.install_live_room(key, chain=chain, normal_days=days)
    if walk == "15":
        import t3_exit_recheck_study as t3
        _, out = t3._job(key)
        rows = out["t3re"]
    else:
        import t1_step_timing_study as tst
        _, rows = tst._job((key, "FIX"))
    return job, rows


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "15", m) for k in INDICES for m in MODES] + [("SENSEX", "5", m) for m in MODES]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for (k, w), figs in list(TESTED.items()) + list(LIVE_FIGS.items()):
        mode = "A" if (k, w) in TESTED and figs == TESTED[(k, w)] else "B"
        s = sds.split(got[(k, w, mode)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} {w} {mode}: must reproduce {figs}"
    W = 172
    pool_ = lambda w, m, ks: sorted([r for k in ks for r in got[(k, w, m)]], key=lambda r: r["when"])
    print("=" * W)
    print(" WHICH PART OF THE LIVE ROOM TO RUN COSTS, AND THE FIXES. Per lot, after costs.  columns: trades, won, total,"
          " profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" SENSEX + MIDCAP (run the rules live), 15-minute walk")
    for m in MODES:
        print(sds.line("   " + LABEL[m], pool_("15", m, RULES_LIVE)))
    print(" SENSEX, 5-minute walk (the step at the candle close)")
    for m in MODES:
        print(sds.line("   " + LABEL[m], got[("SENSEX", "5", m)]))
    for k in INDICES:
        print(f" {k}{'' if k in RULES_LIVE else '  (Trend Rider / TradingView rating live - information)'}, 15-minute walk")
        for m in MODES:
            print(sds.line("   " + LABEL[m], got[(k, "15", m)]))
    print("\n WHAT EACH PART COSTS (against live today, B)")
    print("  " + sds.verdict("C - drop the expected-move cap", pool_("15", "C", RULES_LIVE), pool_("15", "B", RULES_LIVE)).strip())
    print("  " + sds.verdict("D - every day for the normal day", pool_("15", "D", RULES_LIVE), pool_("15", "B", RULES_LIVE)).strip())
    print("\n VERDICT - a fix is kept only if more than live today (B) in BOTH periods, Sensex + Midcap, 15-minute")
    for m in ("C", "E"):
        print("  " + sds.verdict(LABEL[m], pool_("15", m, RULES_LIVE), pool_("15", "B", RULES_LIVE)).strip())
    print("  Sensex 5-minute (beside it):")
    for m in ("C", "E"):
        print("  " + sds.verdict(LABEL[m], got[("SENSEX", "5", m)], got[("SENSEX", "5", "B")]).strip())
    return got


if __name__ == "__main__":
    main()
