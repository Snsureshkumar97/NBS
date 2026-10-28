#!/usr/bin/env python3
"""
sr_study.py — support and resistance, re-tested on the honest backtest (six years) and on real option walls
================================================================================
The user, 28 Oct 2026: "tool is giving the targets but it not looking at ... support resistance", then "2" (re-test
it on the fixed backtest). Earlier support/resistance studies (CPR, pivots, order blocks, strike-close entries,
retests) all dropped - but on the walk that filled stops at their own price and the model that priced options too
rosily near expiry. This re-tests on honest_retest.py's harness (fills.py, pricing.py).

FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026). Levels known at the entry only:
  LEVELS   yesterday's high and low, and its classic floor pivots P = (H+L+C)/3, R1 = 2P-L, S1 = 2P-H, R2 = P+(H-L),
           S2 = P-(H-L)
  ROUND    round numbers - every 100 (Nifty, Midcap), 500 (Bank Nifty, Sensex)
Two uses:
  FILTER x     skip a call whose next level ABOVE the entry is closer than x times the trade's risk (a put: the next
               level below) - x = 1.0 THE CANDIDATE; 0.5 and 1.5 either side
  TARGET       the rules only: out at the next level beyond half the risk, when it comes before T3
on the rules (as they ship) and the TradingView rating (its risk: the Trend Rider's swing stop). BETTER only if more
than today in BOTH periods (to 15 Aug 2025, then held out), the four indices pooled, with the walk-forward / Monte
Carlo line. Per lot after costs.
OPTION WALLS (mode "walls"): the recorder's 7 weeks, real option prices - skip a call when the strike with the most
call open interest within 2% above is closer than y% of the index (a put: put open interest below), y = 0.4 (0.25 and
0.6 either side); the trades kept against the ones skipped. Seven weeks - described, not judged.

RESULT (28 Oct 2026; sr_study_results.txt), per lot after costs, to 15 Aug 2025 / held out, pooled:
  the TradingView rating   today -485,442 / +62,487   LEVELS_1.0 -96,943 / +78,865 KEEP   LEVELS_1.5 +122,255 /
                           +139,842 KEEP (12 of 13 half-years)   LEVELS_0.5 drop   ROUND_1.0 drop
  the rules                today -596,164 / -65,453   LEVELS_1.0 KEEP   LEVELS_1.5 -167,828 / +5,308 KEEP  - but per
                           trade only -114 -> -104 in the first period: it mostly trades less   ROUND drop   TARGET drop
  A filter on a losing system "improves" by trading less, so per trade: the rating -69 -> +46 a trade (first period)
  and +33 -> +193 (held out) with LEVELS_1.5 - it picks better trades. More room required did better at each step
  (0.5 < 1.0 < 1.5). With the 14-day expiry (pricing.MIN_DTE 14): the rating +1,226,589 / +621,547, with LEVELS_1.5
  +640,890 / +316,853 - every index positive in both periods (Nifty +159,618 / +47,343, Bank Nifty +218,813 /
  +71,229, Sensex +119,086 / +17,642, Midcap +143,372 / +180,640), more a trade (+240 / +438 against +173 / +333) and
  a smaller worst drop (75,774 / 51,865 against 96,715 / 67,285) - but LESS in total: it trades a third as often.
  OPTION WALLS (7 weeks, real prices, both systems): the trades with the wall on their side closer than 0.4% lost
  -498 a trade (111), the rest +4 (186); closer than 0.6%: -553 (161) against +253 (136) - the same story as the
  levels, too short to judge alone.

    /usr/bin/python3 sr_study.py                       (HOME at the six-year history)
    /usr/bin/python3 sr_study.py walls OPTION_HISTORY_DIR
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
ROUND_STEP = {"NIFTY": 100, "BANKNIFTY": 500, "SENSEX": 500, "MIDCPNIFTY": 100}
RULES_V = ("TODAY", "LEVELS_1.0", "LEVELS_0.5", "LEVELS_1.5", "ROUND_1.0", "TARGET")
TV_V = ("TODAY", "LEVELS_1.0", "LEVELS_0.5", "LEVELS_1.5", "ROUND_1.0")


from levels import day_levels, next_level   # noqa: E402,F401  (one source of the levels, live and here)


def round_levels(key, price):
    s = ROUND_STEP[key]
    base = np.floor(price / s) * s
    return [base - s, base, base + s, base + 2 * s]


def room_ok(kind, x, key, day_lv, day, price, risk, up):
    if risk is None or risk <= 0:
        return True
    lv = day_lv.get(day, []) if kind == "LEVELS" else round_levels(key, price)
    nxt = next_level(lv, price, up)
    return nxt is None or abs(nxt - price) >= x * risk


def _job(job):
    key, system, v = job
    import honest_retest as h
    if system == "TV":
        import config
        import tv_ratings_study as tvs
        tvs.FIVE_MIN = ()
        if v != "TODAY":
            kind, x = v.split("_")[0], float(v.split("_")[1])
            real = tvs.tv_ratings
            swing = config.TREND_RIDER["swing"]

            def filtered(df, real=real):
                rt = real(df).copy()
                lv = day_levels(df)
                hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
                days = [t.date() for t in df.index]
                r = rt["all"].to_numpy(float).copy()
                for i in range(len(r)):
                    if abs(r[i]) <= tvs.STRONG:
                        continue
                    up = r[i] > 0
                    stop = lo[max(0, i - swing + 1):i + 1].min() if up else hi[max(0, i - swing + 1):i + 1].max()
                    risk = (cl[i] - stop) if up else (stop - cl[i])
                    if not room_ok(kind, x, key, lv, days[i], cl[i], risk, up):
                        r[i] = 0.0
                rt["all"] = r
                return rt
            tvs.tv_ratings = filtered
        return job, h._job((key, "tv", None))[1]["TV_RATING"]
    import order_block_study as ob
    import reversal_exit_study as res
    if v == "TODAY":
        return job, h._job((key, "exit", "TODAY"))[1]
    store = {}

    def make(df, pre):
        store["lv"] = day_levels(df)
        store["days"] = [t.date() for t in df.index]
        if v != "TARGET":
            return None, None, None

        def stop_fn(i, tr):
            ce = tr["side"] == "CE"
            risk = abs(tr["entry"] - tr["stop"])
            nxt = next_level(store["lv"].get(store["days"][i], []), tr["entry"], ce)
            if nxt is None or abs(nxt - tr["entry"]) < 0.5 * risk or tr.get("t3") is None:
                return tr
            if abs(nxt - tr["entry"]) < abs(tr["t3"] - tr["entry"]):
                return dict(tr, t3=nxt)
            return tr
        return None, stop_fn, None
    if v != "TARGET":
        kind, x = v.split("_")[0], float(v.split("_")[1])
        real = res.live_gate

        def gate(k, i, rec, df):
            if not real(k, i, rec, df):
                return False
            if "lv" not in store:
                store["lv"], store["days"] = day_levels(df), [t.date() for t in df.index]
            return room_ok(kind, x, key, store["lv"], store["days"][i], rec.get("spot"), rec.get("risk_points"),
                           rec.get("option_type") == "CE")
        res.live_gate = gate
    return job, ob.rules_rows(key, make)


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [(k, "RULES", v) for k in KEYS for v in RULES_V] + [(k, "TV", v) for k in KEYS for v in TV_V]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    for system, vs in (("RULES", RULES_V), ("TV", TV_V)):
        for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
            R = {v: s([r for k in keys for r in got[(k, system, v)]]) for v in vs}
            print("=" * 120)
            print(f"{scope}  {'the rules' if system == 'RULES' else 'the TradingView rating'}  per lot after costs, "
                  f"honest fills, realistic prices  (to 15 Aug 2025 | held out)")
            for v in vs:
                print(sds.line("     " + v, R[v]))
            for v in vs[1:]:
                text = sds.verdict(f"{v} vs today", R[v], R["TODAY"]).strip()
                print("   " + (text.replace("\n", "\n   ") if scope == "ALL FOUR POOLED" else text.splitlines()[0]))


def walls():
    """The recorder's option walls, on the rating's and the rules' trades of 10 Sep - 26 Oct at real prices."""
    import multiprocessing as mp
    import live_vs_backtest as lv
    import real_price_check as rpc
    P = lv.Prices(sys.argv[2])
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(rpc._job, [(k, s) for k in KEYS for s in ("TV", "RULES")], chunksize=1))
    rows = []
    for (k, system), rr in got.items():
        for r in rr:
            t_in = pd.Timestamp(r["when"])
            if not (rpc.SINCE <= t_in < rpc.UNTIL):
                continue
            r = dict(r, index=k)
            net = rpc.real_net(r, P, 0)
            if net is None:
                continue
            d = t_in.date().isoformat()
            x = P.day(d)
            exps = [e for e in P.expiries(k, d) if e >= d]
            if x is None or not exps:
                continue
            ce = r["side"] == "CE"
            spot = r["entry_spot"]
            opts = x[(x["kind"] == "OPT") & (x["index"] == k) & (x["expiry"] == exps[0]) & (x["opt"] == r["side"])
                     & (x["ts"] <= t_in)]
            if opts.empty:
                continue
            last = opts.sort_values("ts").groupby("strike")["oi"].last().dropna()
            near = last[(last.index > spot) & (last.index <= spot * 1.02)] if ce else \
                last[(last.index < spot) & (last.index >= spot * 0.98)]
            if near.empty:
                continue
            wall = float(near.idxmax())
            rows.append({"system": system, "index": k, "net": net[0], "gap_pct": 100 * abs(wall - spot) / spot})
    d = pd.DataFrame(rows)
    print(f"OPTION WALLS - {len(d)} trades with a recorded chain at their entry minute, 10 Sep - 26 Oct, real prices, "
          f"per lot after costs")
    print(f"   the wall (the strike with the most open interest on the trade's side within 2%) sat a median "
          f"{d.gap_pct.median():.2f}% from the index")
    for y in (0.25, 0.4, 0.6):
        for system in ("TV", "RULES", None):
            g = d if system is None else d[d.system == system]
            blocked = g[g.gap_pct < y]
            kept = g[g.gap_pct >= y]
            name = "both" if system is None else system
            print(f"   wall closer than {y:.2f}%  {name:6s}  kept {len(kept):3d}: {kept.net.sum():+9,.0f} "
                  f"({kept.net.mean():+5,.0f} a trade)   skipped {len(blocked):3d}: {blocked.net.sum():+9,.0f} "
                  f"({blocked.net.mean() if len(blocked) else 0:+5,.0f} a trade)")


if __name__ == "__main__":
    walls() if sys.argv[1:2] == ["walls"] else main()
