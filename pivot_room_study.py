#!/usr/bin/env python3
"""
pivot_room_study.py — room to run decided by the pivot levels that come with the CPR (R1 / R2 / R3 above, S1 / S2 / S3 below)
================================================================================
The user, 21 Oct 2026, after the CPR went on the chart: "with CPR can we decide the room to run", then "yes run it".
Traders take R1 / R2 as targets for a long and S1 / S2 for a short. Room to run (signal_engine.compute_reachability) is
what the rules' targets and their reward:risk gate are built from.

FIXED BEFORE ANY RESULT WAS SEEN (21 Oct 2026). Floor pivots from the previous session (its high H, low L, close C,
built from the 15-minute candles): P = (H+L+C)/3, R1 = 2P-L, R2 = P+(H-L), R3 = H+2(P-L), S1 = 2P-H, S2 = P-(H-L),
S3 = L-2(H-P). The rules as live runs them today (order_block_study.rules_rows: today's room, out at T3 with the steps,
the trail, the 2-hour breakeven, the T1-exit re-entry, per lot after costs; Sensex and Midcap asserted), with the room:
  PIV_NEXT   also capped at the next pivot level in the trade's way (above the price: R1, else R2, else R3; below: S1,
             else S2, else S3; past the third, no cap)
  PIV_R2     also capped at R2 above / S2 below (when the price is under it / over it)
  PIV_ONLY   the distance to the next pivot level IS the room (in place of the expected move and the day's range)
Kept only if more than TODAY in BOTH the first two years and the held-out year, pooled and on each index; robustness.py's
walk-forward and Monte Carlo line under each verdict; and how often trades closed at their target.

RESULT (21 Oct 2026, history to 11 Sep 2026; pivot_room_study_results.txt) - ALL THREE DROP, NOTHING BUILT. Profit
against today, pooled, first two years / held-out: PIV_NEXT -187,406 / -120,328 (walk-forward 1 of 7 half-years),
PIV_R2 -93,076 / -36,457 (2 of 7), PIV_ONLY -145,117 / -100,479 (2 of 7); every index loses in at least one period
(the nearest miss, PIV_R2 on Sensex, is +1,794 held-out but -14,665 in the first two years). Why: the next pivot level
is usually closer than today's room, so the reward:risk gate fails more and the tool takes far fewer trades (1,787 ->
944 in the first two years for PIV_NEXT) - and the ones it skips include the long runners that make the money. Trades
closing AT the target only rose from 2.9% to 4.2%: about 78% of trades end by the trail, the breakeven or the close,
so a nearer target barely changes how they end. Drawdown is smaller (PIV_NEXT about 29-32k less) because there are
half as many trades, not because the trades are better.

    /usr/bin/python3 pivot_room_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
MODES = ("TODAY", "PIV_NEXT", "PIV_R2", "PIV_ONLY")


def pivots(h, l, c):
    p = (h + l + c) / 3.0
    return {"r1": 2 * p - l, "r2": p + (h - l), "r3": h + 2 * (p - l),
            "s1": 2 * p - h, "s2": p - (h - l), "s3": l - 2 * (h - p)}


def caps(lv, spot, mode):
    """(room up, room down) the pivots allow from `spot` under `mode`; None = no cap that way."""
    up_lv = [lv["r1"], lv["r2"], lv["r3"]]
    dn_lv = [lv["s1"], lv["s2"], lv["s3"]]
    if mode == "PIV_R2":
        up = lv["r2"] - spot if lv["r2"] > spot else None
        dn = spot - lv["s2"] if lv["s2"] < spot else None
        return up, dn
    up = next((x - spot for x in up_lv if x > spot), None)
    dn = next((spot - x for x in dn_lv if x < spot), None)
    return up, dn


def _hook(mode):
    def hook(key):
        import pandas as pd
        import backtest_intraday as bt
        import cpr
        import signal_engine as se
        df = bt.fetch_history(key, years=3, use_cache=True)
        sess = cpr.sessions_from(df)
        by_day = {sess[n][0]: pivots(*sess[n - 1][1:]) for n in range(1, len(sess))}
        inner = se.compute_reachability

        def piv(spot, oi, df_, now, *a, **kw):
            out = inner(spot, oi, df_, now, *a, **kw)
            lv = by_day.get(pd.Timestamp(now).date())
            if lv is None:
                return out
            up, dn = caps(lv, spot, mode)
            for side, room in (("up", up), ("down", dn)):
                cur = out.get(f"reach_{side}")
                if mode == "PIV_ONLY":
                    if room is not None:
                        out[f"reach_{side}"], out[f"cap_{side}"] = round(room, 2), "the next pivot level"
                elif room is not None and (cur is None or room < cur):
                    out[f"reach_{side}"], out[f"cap_{side}"] = round(room, 2), "the pivot level"
            out["available"] = out.get("reach_up") is not None or out.get("reach_down") is not None
            return out
        se.compute_reachability = piv
    return hook


def _job(job):
    key, mode = job
    import order_block_study as ob
    return job, ob.rules_rows(key, room_hook=None if mode == "TODAY" else _hook(mode))


def main():
    import multiprocessing as mp
    import pandas as pd
    import order_block_study as ob
    import regime_study as rs
    import stop_day_study as sds
    jobs = [(k, m) for k in RULES for m in MODES]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, figs in ob.B_FIGS.items():
        s = sds.split(got[(k, "TODAY")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} TODAY must be room_fix_study's B {figs}"
    pool_ = lambda m: sorted([r for k in RULES for r in got[(k, m)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" ROOM TO RUN FROM THE PIVOT LEVELS (R1-R3 / S1-S3) - the rules as live runs them, per lot after costs, ₹ "
          "(columns: trades, won, total, profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" NIFTY + SENSEX + MIDCAP (the verdict)")
    for m in MODES:
        print(sds.line("   " + m, pool_(m)))
    for k in RULES:
        print(f" {k}")
        for m in MODES:
            print(sds.line("   " + m, got[(k, m)]))
    print("\n HOW TRADES ENDED (all three) - closed AT THE TARGET / at the stop / otherwise (trail, breakeven, close)")
    for m in MODES:
        rs_ = pool_(m)
        n = len(rs_) or 1
        c = pd.Series([r["closed_via"] for r in rs_]).value_counts()
        print(f"   {m:10s} target {c.get('target', 0) / n:5.1%}   stop {c.get('stop', 0) / n:5.1%}   other {c.get('other', 0) / n:5.1%}"
              f"   of {len(rs_)}")
    print("\n VERDICT - kept only if more than TODAY in BOTH periods (pooled), then on each index")
    for m in MODES[1:]:
        print("  " + sds.verdict(f"{m} pooled", pool_(m), pool_("TODAY")).strip())
        for k in RULES:
            print("      " + sds.verdict(f"{m} {k}", got[(k, m)], got[(k, "TODAY")]).strip())
    return got


if __name__ == "__main__":
    main()
