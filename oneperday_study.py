#!/usr/bin/env python3
"""
oneperday_study.py — one trade a day per index: only the day's first ticket, the rest skipped
================================================================================
The user, 29 Oct 2026: "what if we make one trade per day per index".

FIXED BEFORE ANY RESULT WAS SEEN (29 Oct 2026). Each system's trades as honest_retest.py's harness makes them (stops
filled honestly, option prices corrected to real ones, one ticket at a time per index, cooldowns as live); ONE A DAY
keeps each index's first trade of the day and drops the rest (what a tool that stopped after its first ticket would
have done - its first trade is unchanged). Systems: the TradingView rating, the rating with yesterday's levels
(sr_study 1.5x), the rules, the rating with the levels + the big candle (one ticket at a time, the rating first).
Three ways to book a trade: OPTIONS at the nearest expiry, OPTIONS at least 14 days out (pricing.MIN_DTE 14),
FUTURES (futures_study.booked: points x lot, futures charges, 0.01% slippage a side). Per lot.
BETTER only if ONE A DAY makes more than every trade in BOTH periods (to 15 Aug 2025, then held out), the four
indices pooled, with the walk-forward / Monte Carlo line. Also: the recorder's seven weeks at real option prices
(mode "real").

RESULT (29 Oct 2026; oneperday_study_results.txt). Per lot, pooled, to 15 Aug 2025 / held out:
  where the system loses (the nearest expiry, futures) ONE A DAY is KEEP everywhere - and better a trade, not only
  fewer: the rating at the nearest expiry -485,442 / +62,487 -> -3,132 / +130,871 (-69 -> -1 and +33 -> +123 a trade);
  the rating with the levels +122,255 / +139,842 -> +224,008 / +144,659. The day's first trade is the better one;
  later ones are re-entries in a choppier day.
  where it already makes money (14+ days to run) it keeps about the same profit with a quarter fewer trades and less
  risk - the rating with the levels + the big candle +954,607 / +371,902 -> +941,830 / +343,022 (drop by the rule),
  profit factor 1.35 -> 1.45 / 1.58 -> 1.73, worst drop 100,200 -> 77,760 / 50,467 -> 30,487, worst day -38,530 ->
  -20,079 / -33,190 -> -22,070.
  REAL RECORDED PRICES (10 Sep - 26 Oct, nearest expiry): the rating with the levels + the big candle every trade
  -2,996 (74 trades) -> one a day +21,816 (57); the rating with the levels +7,367 -> +24,637; the rules -4,723 ->
  +1,846; the rating alone -49,795 -> -36,006. Seven weeks, and the levels were chosen on years that include them.

    /usr/bin/python3 oneperday_study.py                         (HOME at the six-year history)
    /usr/bin/python3 oneperday_study.py real OPTION_HISTORY_DIR
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
SYSTEMS = ("TV", "TVL", "RULES", "BC")


def first_per_day(rows):
    """Each index's first trade of each day; the rest dropped."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: pd.Timestamp(r["when"])):
        key = (r.get("index"), pd.Timestamp(r["when"]).date())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _job(job):
    key, system, m = job
    import pricing
    import real_price_check as rpc
    pricing.MIN_DTE = m
    rows = rpc._tv_levels(key)[1] if system == "TVL" else rpc._job((key, system))[1]
    return job, [dict(r, index=key) for r in rows]


def combos(got, m):
    """{name: {index: rows}} - the systems and the rating with the levels + the big candle."""
    import order_block_site_study as obs
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    out = {"TradingView rating": {}, "TradingView + levels": {}, "the rules": {}, "TradingView + levels + big candle": {}}
    for k in KEYS:
        tv, tl, ru, bc = (s(got[(k, x, m)]) for x in ("TV", "TVL", "RULES", "BC"))
        out["TradingView rating"][k] = tv
        out["TradingView + levels"][k] = tl
        out["the rules"][k] = ru
        out["TradingView + levels + big candle"][k] = s(obs.slot(tl, bc)) if k != "MIDCPNIFTY" else tl
    return out


def main():
    import multiprocessing as mp
    import futures_study as fs
    import stop_day_study as sds
    jobs = [(k, sy, m) for m in (0, 14) for k in KEYS for sy in SYSTEMS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    C = {m: combos(got, m) for m in (0, 14)}
    for name in C[0]:
        print("=" * 120)
        print(f"{name.upper()} - all four pooled, per lot  (to 15 Aug 2025 | held out)")
        for label, rows_of in (("OPTIONS, nearest expiry", lambda k: C[0][name][k]),
                               ("OPTIONS, 14+ days", lambda k: C[14][name][k]),
                               ("FUTURES", lambda k: fs.booked(C[0][name][k], k))):
            every = s([r for k in KEYS for r in rows_of(k)])
            one = s(first_per_day(every))
            print(f"  {label}")
            print(sds.line("     every trade", every))
            print(sds.line("     ONE A DAY", one))
            text = sds.verdict("one a day vs every trade", one, every).strip()
            print("   " + text.replace("\n", "\n   "))
    print()
    print("ONE A DAY BY INDEX - the rating with the levels + the big candle, OPTIONS 14+ days (to 15 Aug 2025 | held out)")
    for k in KEYS:
        every = C[14]["TradingView + levels + big candle"][k]
        print(sds.line(f"   {k:11s} every trade", every))
        print(sds.line(f"   {k:11s} ONE A DAY", first_per_day(every)))


def real():
    """The recorder's seven weeks at real option prices: every trade against one a day."""
    import multiprocessing as mp
    import live_vs_backtest as lv
    import real_price_check as rpc
    P = lv.Prices(sys.argv[2])
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, [(k, sy, 0) for k in KEYS for sy in SYSTEMS], chunksize=1))
    C = combos(got, 0)
    print(f"REAL RECORDED OPTION PRICES, {rpc.SINCE.date()} to {(rpc.UNTIL - pd.Timedelta(days=1)).date()}, nearest "
          f"expiry and the next recorded one, per lot after costs")
    for name, by in C.items():
        every = [r for k in KEYS for r in by[k] if rpc.SINCE <= pd.Timestamp(r["when"]) < rpc.UNTIL]
        for label, rows in (("every trade", every), ("ONE A DAY", first_per_day(every))):
            near = [rpc.real_net(r, P, 0) for r in rows]
            nxt = [rpc.real_net(r, P, 1) for r in rows]
            n0 = [a[0] for a in near if a]
            n1 = [b[0] for b in nxt if b]
            print(f"   {name:34s} {label:12s} {len(rows):4d} trades | nearest {sum(n0):+9,.0f} "
                  f"({100 * sum(x > 0 for x in n0) / max(len(n0), 1):3.0f}% won) | next expiry {sum(n1):+9,.0f}")


if __name__ == "__main__":
    real() if sys.argv[1:2] == ["real"] else main()
