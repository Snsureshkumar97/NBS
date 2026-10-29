#!/usr/bin/env python3
"""
futures_study.py — the same signals traded on index FUTURES instead of options (six years, all four indices)
================================================================================
The user, 28 Oct 2026: "you cant find no startegy that works better for indian option trading", then "yes run the
futures test". With option prices corrected to real ones (pricing.py), buying the nearest expiry loses on the weekly
indices whatever the signal; the far-expiry results rest on a model with thin real evidence. A future has no time
decay and no pricing model: a trade makes the index points it caught. If the signals have an edge, futures show it;
if they do not, no choice of option will rescue them.

FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026). Every system's own entries and exits - honest_retest.py's harness
(honestly filled stops, one ticket at a time per index, cooldowns as live) - but each trade booked as one lot of the
index future: (exit - entry) x the side x today's lot size, after Zerodha's futures charges (brokerage 20 an order,
STT 0.02% of the sell side, exchange 0.00173% and SEBI 10/crore a side, stamp 0.002% of the buy, GST 18% on
brokerage + exchange + SEBI) and SLIPPAGE 0.01% of the price on the way in and again on the way out (and once more at
0.02% to see it hold). The future's basis to the index is left out (it barely moves inside a day).
  the rules (as they ship)       the TradingView rating        the rating with yesterday's levels (sr_study 1.5x)
  the big candle                 the rating + the big candle   the rating with the levels + the big candle
  the COIN (the rules' gates and exits, a coin for the direction): it must lose
WORKS only if it makes money in BOTH periods (to 15 Aug 2025, then held out), the four indices pooled, beats the coin,
and holds up on robustness.py's walk-forward / Monte Carlo. Per lot of the future - margin about 1-2 lakh a lot.

RESULT (28 Oct 2026; futures_study_results.txt). The signals DO catch the index's move - points a trade, pooled: the
coin -2.7, the rules +3.6, the TradingView rating +6.6, with the levels +12.3, the big candle +16.3, the rating with
the levels and the big candle +13.7 - a real edge, but small. A future's round trip costs about 12 Nifty points
(STT 0.02% of the sell leg is the most of it, then the slippage assumed): NOTHING makes money in both periods
pooled - the rating with the levels -512,439 / -3,924, the big candle -132,537 / +85,064, the rules -2,568,914 /
-905,886, the coin -6,849,614 / -2,673,757. Only Midcap: the rating with the levels +28,177 / +181,266, with the big
candle too +176,122 / +72,596 (at 0.02% slippage +64,815 / +558) - one index, thin. Why the options came out ahead
with 14+ days to run (pricing.py): an option's costs on the same exposure are far lower than a future's, and its
curve pays on the big moves - so that result stands or falls on real far-expiry prices.

    /usr/bin/python3 futures_study.py        (HOME at the six-year history)
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
SLIP = 0.0001


def charges(entry, exit_, qty, slip):
    """Zerodha's futures charges for one lot, in and out, plus slippage both ways - rupees."""
    buy, sell = entry * qty, exit_ * qty                 # turnover either way round, side does not matter for these
    brokerage = 40.0
    stt = 0.0002 * max(buy, sell)                        # on the sell leg
    txn = 0.0000173 * (buy + sell)
    sebi = 10 / 1e7 * (buy + sell)
    stamp = 0.00002 * min(buy, sell)                     # on the buy leg
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + stt + txn + sebi + stamp + gst + slip * (buy + sell)


def booked(rows, key, slip=SLIP):
    import config
    qty = config.INSTRUMENTS[key]["lot_size"]
    out = []
    for r in rows:
        e, x = float(r["entry_spot"]), float(r["exit_spot"])
        pts = (x - e) * (1 if r["side"] == "CE" else -1)
        out.append(dict(r, index=key, pts=pts, net=pts * qty - charges(e, x, qty, slip)))
    return out


def _coin(key):
    """The coin: the rules' gates and exits, a coin for the direction (votes_alone_study COIN_A_ONLY), with the
    entry and exit levels kept."""
    import order_block_study as ob
    import t3_exit_recheck_study as t3
    import votes_alone_study as vas
    vas.setup("COIN_A_ONLY")
    real, seen = t3.sim, {}

    def sim(A, i, tr, st, target, **kw):
        legs, how, t1 = real(A, i, tr, st, target, **kw)
        seen[i] = (tr["entry"], legs[-1][0])
        return legs, how, t1
    t3.sim = sim
    make = lambda df, pre: (None, None, lambda i, p: p.update(entry_spot=seen[i][0], exit_spot=seen[i][1]))
    return ob.rules_rows(key, make)


def _job(job):
    key, system = job
    import real_price_check as rpc
    if system == "TVL":
        return job, rpc._tv_levels(key)[1]
    if system == "COIN":
        return job, _coin(key)
    return job, rpc._job((key, system))[1]


def main():
    import multiprocessing as mp
    import order_block_site_study as obs
    import stop_day_study as sds
    jobs = [(k, s) for k in KEYS for s in ("RULES", "TV", "TVL", "BC", "COIN")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    for slip in (SLIP, 2 * SLIP):
        R = {}
        for k in KEYS:
            b = {sy: s(booked(got[(k, sy)], k, slip)) for sy in ("RULES", "TV", "TVL", "BC", "COIN")}
            R[k] = {"the rules": b["RULES"], "TradingView rating": b["TV"], "TradingView + levels": b["TVL"],
                    "the big candle": b["BC"], "TradingView + big candle": s(obs.slot(b["TV"], b["BC"])),
                    "TradingView + levels + big candle": s(obs.slot(b["TVL"], b["BC"])), "COIN (control)": b["COIN"]}
        names = list(R["NIFTY"])
        print("#" * 120)
        print(f"INDEX FUTURES, one lot, after charges and {100 * slip:.2f}% slippage a side  (to 15 Aug 2025 | held out)")
        for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
            print("=" * 120)
            print(scope)
            for n in names:
                rows = s([r for k in keys for r in R[k][n]])
                pts = pd.Series([r["pts"] for r in rows])
                print(sds.line(f"   {n:34s}", rows)[:150] + f"   {pts.mean():+6.1f} pts a trade")
            if scope == "ALL FOUR POOLED" and slip == SLIP:
                zero = lambda rows: [dict(r, net=0.0) for r in rows]
                for n in names[:-1]:
                    rows = s([r for k in keys for r in R[k][n]])
                    text = sds.verdict(f"{n} vs doing nothing", rows, zero(rows)).strip()
                    print("   " + text.replace("\n", "\n   "))


if __name__ == "__main__":
    main()
