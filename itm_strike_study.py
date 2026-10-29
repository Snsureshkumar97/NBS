#!/usr/bin/env python3
"""
itm_strike_study.py — which strike to buy, re-tested on the honest harness: at the money, 1 or 2 in, 1 out
================================================================================
The user, 29 Oct 2026: "yes start the ITM test". strike_choice_study (13 Oct 2026, nbs-strike-choice-atm-stays) kept
the at-the-money strike - but on the old harness (stops filled at the stop when the candle opened past it) and the
old option pricing (optimistic near expiry), both fixed on 28 Oct. An in-the-money option moves more with the index
(a higher delta) and loses less of its price to time decay; it costs more a lot.

FIXED BEFORE ANY RESULT WAS SEEN (29 Oct 2026). debit_spread_study's trades (honest_retest.py's harness: stops filled
honestly, one ticket at a time per index, cooldowns as live) for the same three systems - the user's setup (the
rating with yesterday's levels + the big candle; Midcap the rating with the levels), the same one trade a day, the
rating alone - each booked four ways at the same entry and exit minutes, per lot, after every charge and 0.25%
slippage a side (regime_study.net_rupees):
  ATM (today)   ITM1 / ITM2 (one / two strikes in the money)   OTM1 (one out, for reference)
Six years on the corrected model (pricing.py), at the nearest expiry and at least 14 days out. Then the recorder's
REAL prices, 10 Sep - 26 Oct 2026 (nearest and next recorded expiry), every strike from a candle at most 5 minutes
old (debit_spread_study.fresh), each strike against ATM on the trades priced every way.
BETTER only if it makes more than ATM per lot in BOTH periods, the four indices pooled, holds up on robustness.py's
walk-forward / Monte Carlo, and is no worse than ATM on the real prices. Shown too: the premium a lot costs, and the
profit per Rs 1 lakh of premium (a fixed sum buys fewer in-the-money lots).

RESULT (29 Oct 2026; itm_strike_study_results.txt). NOT ADOPTED - the model's gain does not show on real prices.
  NEAREST EXPIRY, the model, per lot, to 15 Aug 2025 / held out - in the money wins every system, walk-forward 10-13
  of 13: the user's setup ATM +160,000 / +152,775, ITM1 +237,915 / +182,991, ITM2 +327,632 / +213,259 (+51 and +72
  a trade on ATM), OTM1 +115,252 / +127,608; one a day ATM +214,527 / +141,070, ITM2 +328,502 / +194,222; the rating
  alone ATM -485,442 / +62,487, ITM2 -288,523 / +100,077. Per lakh of premium too (setup +762 -> +1,001).
  14+ DAYS: no difference (the setup ATM +954,607 / +371,902, ITM2 +944,359 / +373,461) - far from expiry a strike
  or two either side moves almost alike.
  REAL PRICES, nearest expiry, the trades priced every way: the setup ATM -2,996, ITM1 -3,887, ITM2 -3,190 (61
  trades) - where the model on those same trades says ATM -1,107, ITM2 +5,662: it flatters ITM2 by about Rs 110 a
  trade, more than its six-year edge. One a day ATM +21,816, ITM2 +24,613 (45); the rating alone ATM -49,062, ITM2
  -60,467 (164). Next recorded expiry: noise (ATM the worst of all four, OTM1 near the best).
  So: keep ATM. Check again on real prices once the recorder (every strike within 6% since 28 Oct) has a few weeks.

    HOME=<six-year history> /usr/bin/python3 itm_strike_study.py [OPTION_HISTORY_DIR]
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debit_spread_study as dss

KEYS = dss.KEYS
WAYS = {"ATM": 0, "ITM1": 1, "ITM2": 2, "OTM1": -1}          # strikes in the money
SLIP = dss.SLIP


def strike(K, n, step, call):
    """n strikes in the money from the at-the-money K (negative: out of it)."""
    return K - n * step if call else K + n * step


def model_net(r, days, n):
    """(net rupees per lot, premium paid per lot) on pricing.py's corrected model; None if worthless at entry."""
    import config
    import pricing
    import pro_study as ps
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    exp = ps.trade_expiry(k, t_in.date(), days)
    call = r["side"] == "CE"
    K = strike(round(r["entry_spot"] / step) * step, n, step, call)
    p0 = pricing.premium(r["entry_spot"], K, exp, t_in, r["sigma"], call)
    if p0 <= 0.5:
        return None
    p1 = pricing.premium(r["exit_spot"], K, exp, t_out, r["sigma"], call)
    return rs.net_rupees(p0, p1, qty, exch, SLIP), p0 * qty


def real_net(r, P, which, n):
    """(net per lot, premium per lot) on recorded prices, every price from a candle at most dss.FRESH_MIN minutes
    old; None when the recording has none."""
    import config
    import regime_study as rs
    k = r["index"]
    meta = config.INSTRUMENTS[k]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
    d = t_in.date().isoformat()
    exps = [e for e in P.expiries(k, d) if e >= d]
    if len(exps) <= which:
        return None
    exit_t = t_out - pd.Timedelta(minutes=1)                  # as real_price_check.real_net
    if r["how"] == "level":
        idx = P.index(k, d)
        if idx is not None and len(idx):
            win = idx[(idx.index >= t_out - pd.Timedelta(minutes=15)) & (idx.index < t_out)]
            hit = win[(win["low"] <= r["exit_spot"]) & (win["high"] >= r["exit_spot"])]
            if len(hit):
                exit_t = hit.index[0]
            elif len(win):
                exit_t = win.index[0]
    K = strike(round(r["entry_spot"] / step) * step, n, step, r["side"] == "CE")
    opt = P.option(k, d, exps[which], K, r["side"])
    p0, p1 = dss.fresh(opt, t_in, "open"), dss.fresh(opt, exit_t, "close")
    if p0 is None or p1 is None or p0 <= 0.5:
        return None
    return rs.net_rupees(p0, p1, qty, exch, SLIP), p0 * qty


def _job(job):
    """debit_spread_study's trades, each priced every way at the nearest expiry (m0) and 14+ days (m14)."""
    import backtest_intraday as bt
    import pricing
    key, _ = job
    _, (rows, lost) = dss._job(job)
    days = set(bt.fetch_history(key, years=3, use_cache=True).index.date)
    for m in (0, 14):
        pricing.MIN_DTE = m
        for r in rows:
            r[f"s{m}"] = {w: model_net(r, days, n) for w, n in WAYS.items()}
    pricing.MIN_DTE = 0
    return job, (rows, lost)


def as_rows(rows, m, way):
    """The trades priced EVERY way, with net = that way's."""
    ok = [r for r in rows if all(v is not None for v in r[m].values())]
    return [dict(r, when=pd.Timestamp(r["when"]), net=r[m][way][0], paid=r[m][way][1]) for r in ok]


def main():
    import multiprocessing as mp
    import live_vs_backtest as lv
    import stop_day_study as sds
    jobs = [(k, sy) for k in KEYS for sy in ("TV", "TVL", "BC")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    lost = {j: v[1] for j, v in got.items() if v[1]}
    print(f"trades left out (no targets captured): {lost or 'none'}")
    tv0 = [r for k in KEYS for r in got[(k, "TV")][0] if r["s0"]["ATM"] is not None]
    gap = max((abs(r["s0"]["ATM"][0] - r["net"]) for r in tv0), default=0.0)
    print(f"check: ATM at the nearest expiry against the harness's own price, the rating's {len(tv0)} trades - "
          f"largest difference Rs {gap:,.2f}")
    S = dss.systems(got)
    for name, rows in S.items():
        for m, label in (("s0", "NEAREST EXPIRY"), ("s14", "14+ DAYS TO EXPIRY")):
            print("=" * 172)
            print(f"{name.upper()} - {label}, the model, all four pooled, per lot   (to 15 Aug 2025 | held out)")
            base = as_rows(rows, m, "ATM")
            for w in WAYS:
                R = as_rows(rows, m, w)
                paid = pd.Series([r["paid"] for r in R]).mean()
                per_lakh = 1e5 * sum(r["net"] for r in R) / max(sum(r["paid"] for r in R), 1.0)
                print(sds.line(f"   {w}", R) + f"   premium a lot Rs {paid:,.0f}, Rs {per_lakh:+,.0f} a lakh paid")
            for w in ("ITM1", "ITM2", "OTM1"):
                text = sds.verdict(f"{w} vs ATM", as_rows(rows, m, w), base).strip()
                print("   " + text.replace("\n", "\n   "))
            by = lambda w, k: sum(r["net"] for r in as_rows(rows, m, w) if r["index"] == k)
            print("   by index, six years:  " + "   ".join(
                f"{k} " + " / ".join(f"{w} {by(w, k):+,.0f}" for w in WAYS) for k in KEYS))
    if len(sys.argv) < 2:
        return
    import real_price_check as rpc
    P = lv.Prices(sys.argv[1])
    print("=" * 172)
    print(f"REAL RECORDED PRICES, {rpc.SINCE.date()} to {(rpc.UNTIL - pd.Timedelta(days=1)).date()} - per lot, after "
          f"charges and 0.25% slippage a side, every strike from a candle at most {dss.FRESH_MIN} minutes old, on the "
          f"trades priced every way (and the model on those same trades, nearest expiry)")
    for name, rows in S.items():
        win = [r for r in rows if rpc.SINCE <= pd.Timestamp(r["when"]) < rpc.UNTIL]
        for which, label in ((0, "nearest"), (1, "next recorded")):
            priced = [(r, {w: real_net(r, P, which, n) for w, n in WAYS.items()}) for r in win]
            ok = [(r, v) for r, v in priced if all(x is not None for x in v.values())]
            cells = []
            for w in WAYS:
                tot = sum(v[w][0] for _, v in ok)
                won = 100 * sum(v[w][0] > 0 for _, v in ok) / max(len(ok), 1)
                mod = (f" (model {sum(r['s0'][w][0] for r, _ in ok if r['s0'][w] is not None):+,.0f})"
                       if which == 0 else "")
                cells.append(f"{w} {tot:+9,.0f} {won:3.0f}%{mod}")
            print(f"   {name[:34]:34s} {label:14s} {len(ok):3d} of {len(win):3d} priced | " + " | ".join(cells))


if __name__ == "__main__":
    main()
