#!/usr/bin/env python3
"""
cpr_study.py — the Central Pivot Range: does it say what kind of day is coming, and would it help the tool's trades?
================================================================================
The user, 21 Oct 2026: "what is CPR how is it usefull when we are doing option trading", then "show it and give simple
explanation" and "test it as well". The traders' claim: a NARROW CPR (against recent days) comes before a trending,
bigger day - good for option buyers; a WIDE one before a sideways day; and price above the CPR favours calls, below it
puts. cpr.py is the one definition (the page uses it too): narrow <= 0.6x, wide >= 1.4x the previous 10 sessions'
average CPR width.

FIXED BEFORE ANY RESULT WAS SEEN (21 Oct 2026). 3 years of 15-minute candles, sessions built from them.
  A. THE DAY. For every session, its CPR (from the session before) and what the day then did: its range against the
     previous 10 sessions' average range, how much of that range was one-way (|close - open| / range), and whether it
     closed outside the CPR. Narrow / normal / wide side by side, per index.
  B. THE TRADES, each index on the system it runs live: the rules on Nifty, Sensex and Midcap (order_block_study.
     rules_rows - today's room, out at T3 with the steps, the trail, the breakeven, the T1-exit re-entry, per lot after
     costs; Sensex and Midcap asserted), Bank Nifty's TradingView rating (tv_ratings_study tv_ours, asserted). One more
     entry condition each (a blocked signal frees the slot, as live):
       SKIP_WIDE    no entry on a wide-CPR day
       NARROW_ONLY  entries only on a narrow-CPR day
       SIDE         a call only while the price is above the CPR, a put only below it (inside it, none)
     Kept only if more than today in BOTH the first two years and the held-out year, the rules' three indices pooled,
     then on each; robustness.py's walk-forward and Monte Carlo line under each verdict.

RESULT (21 Oct 2026, history to 11 Sep 2026; cpr_study_results.txt) - NOTHING BUILT; CPR is shown as a reference only.
  A. THE TRADERS' CLAIM DOES NOT HOLD. All four indices, ~2,900 sessions: after a NARROW CPR the day's range was 0.98x the
     10-day average (27% big days), after a normal one 1.01x (26%), after a WIDE one 1.11x (34%) - wide days moved MORE,
     the opposite of the claim (a wide CPR follows a wide day, and big days cluster). The one-way share was ~48% for all
     three. Narrow days "closed outside the CPR" more (96% vs 81%) only because a narrow band is easy to leave.
  B. EVERY FILTER LOSES, in both periods, pooled and on every index. Profit against today, first two years / held-out:
     rules (Nifty + Sensex + Midcap) SKIP_WIDE -126,276 / -57,081, NARROW_ONLY -261,892 / -86,722, SIDE -25,944 /
     -35,906; Bank Nifty's TradingView rating -103,293 / -4,449, -124,233 / -60,452, -23,363 / -11,090.
The page's plain words (web_server cprSay) say what traders expect and what this test found.

    /usr/bin/python3 cpr_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
ALL = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
VARIANTS = ("TODAY", "SKIP_WIDE", "NARROW_ONLY", "SIDE")


def cpr_by_day(df):
    """{session date: cpr.levels(...) built from the sessions before it} for every session in the 15-minute df."""
    import cpr
    sess = cpr.sessions_from(df)
    out = {}
    for n in range(4, len(sess)):
        out[sess[n][0]] = cpr.levels(sess[:n])
    return out


def allowed(v, levels, close, buy):
    if v == "TODAY" or levels is None or levels.get("kind") is None:
        return True
    if v == "SKIP_WIDE":
        return levels["kind"] != "wide"
    if v == "NARROW_ONLY":
        return levels["kind"] == "narrow"
    if v == "SIDE":
        return close > levels["tc"] if buy else close < levels["bc"]
    raise ValueError(v)


def _rules_job(job):
    key, v = job
    import order_block_study as ob

    def make(df, pre):
        if v == "TODAY":
            return None, None, None
        by = cpr_by_day(df)
        cl = df["Close"].to_numpy()
        days = [t.date() for t in df.index]
        return (lambda i, buy: allowed(v, by.get(days[i]), float(cl[i]), buy)), None, None
    return job, ob.rules_rows(key, make)


def _tv_job(job):
    _, v = job
    import tv_ratings_study as tvs
    if v != "TODAY":
        real = tvs.tv_ratings

        def filtered(df, real=real):
            rt = real(df).copy()
            by = cpr_by_day(df)
            cl = df["Close"].to_numpy()
            days = [t.date() for t in df.index]
            r = rt["all"].to_numpy(float).copy()
            for i in range(len(r)):
                if (r[i] > tvs.STRONG and not allowed(v, by.get(days[i]), float(cl[i]), True)) or \
                   (r[i] < -tvs.STRONG and not allowed(v, by.get(days[i]), float(cl[i]), False)):
                    r[i] = 0.0
            rt["all"] = r
            return rt
        tvs.tv_ratings = filtered
    _, out = tvs._tv_job("BANKNIFTY")
    return job, out["tv_ours"]


def day_table():
    """Part A: per index, per CPR kind - days, the range against the 10-day average, how one-way, closed outside."""
    import backtest_intraday as bt
    import cpr
    rows = []
    for key in ALL:
        df = bt.fetch_history(key, years=3, use_cache=True)
        sess = cpr.sessions_from(df)
        opens = {}
        for ts, o in zip(df.index, df["Open"]):
            opens.setdefault(ts.date(), float(o))
        for n in range(11, len(sess)):
            lv = cpr.levels(sess[:n])
            d, h, l, c = sess[n]
            rng = h - l
            avg = sum(x[1] - x[2] for x in sess[n - 10:n]) / 10
            if not lv or not lv["kind"] or rng <= 0 or avg <= 0:
                continue
            rows.append({"index": key, "kind": lv["kind"], "range_x": rng / avg,
                         "one_way": abs(c - opens[d]) / rng, "outside": c > lv["tc"] or c < lv["bc"],
                         "big": rng >= 1.2 * avg})
    return rows


def main():
    import multiprocessing as mp
    import pandas as pd
    import order_block_study as ob
    import stop_day_study as sds
    days = pd.DataFrame(day_table())
    W = 160
    print("=" * W)
    print(" A. WHAT KIND OF DAY FOLLOWS: each session's CPR (from the session before) and what the day then did, 3 years")
    print("=" * W)
    print(f" {'':12s} {'CPR':8s} {'days':>6s} {'range vs 10-day avg':>20s} {'big day (1.2x+)':>16s} {'one-way share':>14s} {'closed outside CPR':>19s}")
    for key in ALL + ("ALL",):
        g0 = days if key == "ALL" else days[days["index"] == key]
        for kind in ("narrow", "normal", "wide"):
            g = g0[g0["kind"] == kind]
            if len(g):
                print(f" {key:12s} {kind:8s} {len(g):>6} {g['range_x'].mean():>19.2f}x {100 * g['big'].mean():>15.0f}%"
                      f" {100 * g['one_way'].mean():>13.0f}% {100 * g['outside'].mean():>18.0f}%")
    jobs = [(k, v) for k in RULES for v in VARIANTS]
    tv_jobs = [("BANKNIFTY", v) for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_rules_job, jobs, chunksize=1))
        got.update({("BANKNIFTY", v): rows for (_, v), rows in pool.map(_tv_job, tv_jobs, chunksize=1)})
    for k, figs in ob.B_FIGS.items():
        s = sds.split(got[(k, "TODAY")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} TODAY must be room_fix_study's B {figs}"
    s = sds.split(got[("BANKNIFTY", "TODAY")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == ob.TV_FIGS, "Bank Nifty TODAY must be tv_ours"
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, v)]], key=lambda r: r["when"])
    print("\n" + "=" * W)
    print(" B. THE TRADES - each index on its live system, per lot after costs, ₹ (columns: trades, won, total, profit factor,"
          " worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" RULES: NIFTY + SENSEX + MIDCAP (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in RULES + ("BANKNIFTY",):
        print(f" {k}" + ("  (TradingView rating)" if k == "BANKNIFTY" else ""))
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print("\n VERDICT - kept only if more than TODAY in BOTH periods (pooled), then on each index")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(f"{v} rules pooled", pool_(v), pool_("TODAY")).strip())
        for k in RULES + ("BANKNIFTY",):
            print("      " + sds.verdict(f"{v} {k}", got[(k, v)], got[(k, "TODAY")]).strip())
    return got


if __name__ == "__main__":
    main()
