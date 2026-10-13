#!/usr/bin/env python3
"""
strike_choice_study.py — buying the in-the-money strike instead of the at-the-money one (3 years, the four indices)
================================================================================
The user, 13 Oct 2026: "test the in the money strike".

Every ticket today buys the strike nearest the index (at the money). An in-the-money option costs more, decays less
in proportion and moves more with the index (a higher delta); an out-of-the-money one the reverse. The strike does
not change WHEN a trade enters or exits - those are on the index - only what the option costs and pays, so the very
same trades are priced under each strike.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026). m strike steps in the money (a call's strike m steps BELOW the
nearest, a put's m steps ABOVE):
    ATM     m = 0   today
    ITM 1   m = 1
    ITM 2   m = 2
    OTM 1   m = -1  (for comparison)
Each index under its own live system, as its own study prices it - Sensex and Midcap Select on the tool's rules
(bollinger_vote_study's harness: live entry checks and exit, one position with the cooldown waiver; "ATM" asserted to
its baseline), Nifty and Bank Nifty on the Trend Rider (trend_rider_t1_study's 5-minute walk with today's near-target
ladder). Black-Scholes at that day's VIX-scaled volatility, real expiries (Midcap monthly), 0.25% slippage a side on
whichever premium, every charge, per lot.

LIMITS, said up front: one volatility for every strike - the real smile (puts dearer below the money, calls above) is
not in it; and an in-the-money strike trades thinner, with a wider spread than the 0.25% assumed here. Both flatter
the in-the-money result a little.

VERDICT: a strike is KEPT for a system only if it makes more than ATM in BOTH the first two years and the held-out
final year, that system's two indices pooled.

RESULT (13 Oct 2026, history to 11 Sep 2026, per lot after costs; strike_choice_study_results.txt) - ATM STAYS, every
other strike DROP, on both systems and all four indices pooled (first two years / held-out):
    all four   ATM +591,306 / +301,701;  ITM 1 -31,053 / -14,555;  ITM 2 -99,082 / -45,926;  OTM 1 -34,411 / -40,136
    rules      ITM 1 -7,047 / -6,252 (Midcap's held-out alone +1,907)      Trend Rider  ITM 1 -24,006 / -8,303
  Deeper in the money is worse on every index; one step out of the money cuts the worst drop but earns less. A first
  run had shown ITM 1 ahead on Nifty + Bank Nifty - a leftover patched strike from a reused worker (see _patch); it
  was discarded, and "ATM" is now asserted on all four indices.

    python3 strike_choice_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("SENSEX", "MIDCPNIFTY")
TREND = ("NIFTY", "BANKNIFTY")
MONEYNESS = (("ATM", 0), ("ITM 1", 1), ("ITM 2", 2), ("OTM 1", -1))
T_TODAY = "today: 90% to T2 -> T1, 90% to T3 -> T2"
# the Trend Rider's live exit, 5-minute walk, at the money - exit_ladder_study_results.txt (13 Oct 2026)
TREND_TODAY = {"NIFTY": (179191, 81254), "BANKNIFTY": (159890, 33779)}


def strike(entry, step, call, m):
    """The strike m steps in the money from the one nearest the index."""
    k0 = round(entry / step) * step
    return k0 - m * step if call else k0 + m * step


_ORIG = {}


def _patch(m):
    """pro_study.price (the rules' harness) and vwap_ema_atr_study.price_trade (the Trend Rider's) with the strike m
    steps in the money - each otherwise line for line as it is. Set EVERY time, the studies' own functions restored for
    m = 0: a worker process runs several jobs, and a strike left patched by the last one priced the next (found on the
    first run, 13 Oct 2026 - two "ATM" rows came out equal to other strikes; that run was discarded)."""
    import datetime as dt
    import pandas as pd
    import config
    import pro_study as ps
    import regime_study as rs
    import vwap_ema_atr_study as vea
    _ORIG.setdefault("price", ps.price)
    _ORIG.setdefault("price_trade", vea.price_trade)

    def price(key, df, A, i, tr, legs, sigma, next_expiry_on_expiry_day=False):
        meta = config.INSTRUMENTS[key]
        step, qty = meta["strike_step"], meta["lot_size"]
        exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
        when = df.index[i] + pd.Timedelta(minutes=15)
        exp = ps.expiry_on_or_after(key, when.date(), A["days"])
        if next_expiry_on_expiry_day and exp == when.date():
            exp = ps.expiry_on_or_after(key, when.date() + dt.timedelta(days=1), A["days"])
        call = tr["side"] == "CE"
        K = strike(tr["entry"], step, call, m)
        p0 = rs.bs(tr["entry"], K, ps.years_to(exp, when), sigma, call)
        if p0 <= 0.5:
            return None
        buy = p0 * (1 + ps.SLIP) * qty
        sells, orders = 0.0, 1
        for spot, j, w in legs:
            t_exit = df.index[j] + pd.Timedelta(minutes=15)
            p1 = rs.bs(spot, K, ps.years_to(exp, t_exit), sigma, call)
            sells += max(p1 * (1 - ps.SLIP), 0.0) * qty * w
            orders += 1
        brokerage = rs.BROKERAGE_PER_ORDER * orders
        stt = rs.STT_SELL * sells
        txn = rs.TXN[exch] * (buy + sells)
        sebi = rs.SEBI_PER_RUPEE * (buy + sells)
        stamp = rs.STAMP_BUY * buy
        gst = rs.GST * (brokerage + txn + sebi)
        net = sells - buy - (brokerage + stt + txn + sebi + stamp + gst)
        exit_bar = max(j for _, j, _ in legs)
        return {"when": when, "exit_time": df.index[exit_bar] + pd.Timedelta(minutes=15),
                "net": net, "expiry_day": exp == when.date(), "index": key}

    def price_trade(key, days, t_in, s_in, t_out, s_out, sigma, call):
        meta = config.INSTRUMENTS[key]
        step, qty = meta["strike_step"], meta["lot_size"]
        exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
        exp = ps.expiry_on_or_after(key, t_in.date(), days)
        K = strike(s_in, step, call, m)
        p0 = rs.bs(s_in, K, ps.years_to(exp, t_in), sigma, call)
        if p0 <= 0.5:
            return None
        p1 = rs.bs(s_out, K, ps.years_to(exp, t_out), sigma, call)
        buy = p0 * (1 + ps.SLIP) * qty
        sells = max(p1 * (1 - ps.SLIP), 0.0) * qty
        brokerage = rs.BROKERAGE_PER_ORDER * 2
        stt = rs.STT_SELL * sells
        txn = rs.TXN[exch] * (buy + sells)
        sebi = rs.SEBI_PER_RUPEE * (buy + sells)
        stamp = rs.STAMP_BUY * buy
        gst = rs.GST * (brokerage + txn + sebi)
        return sells - buy - (brokerage + stt + txn + sebi + stamp + gst)

    ps.price = price if m != 0 else _ORIG["price"]          # at the money: the studies' own functions
    vea.price_trade = price_trade if m != 0 else _ORIG["price_trade"]


def _rules_job(job):
    key, (label, m) = job
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    _patch(m)
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    rows = ows.candidates(key, df, lambda i, rec: res.live_gate(key, i, rec, df), A, F, pre, st, target, True)
    return job, rows


def _trend_job(job):
    key, (label, m) = job
    _patch(m)
    import trend_rider_t1_study as trs
    RR = trs.RR
    trs.VARIANTS = [("no steps (the study's own check)", None, None, False),
                    (T_TODAY, [(RR * 2 / 3 * 0.9, RR / 3), (RR * 0.9, RR * 2 / 3)], "ladder", False)]
    k, out = trs._job(key)
    return job, out[(T_TODAY, "5")]


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=6) as ex:
        r = ex.map(_rules_job, [(k, mm) for k in RULES for mm in MONEYNESS])
        t = ex.map(_trend_job, [(k, mm) for k in TREND for mm in MONEYNESS])
        got = dict(r)
        got.update(dict(t))
    for k in RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(got[(k, MONEYNESS[0])])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: ATM must reproduce the baseline"
    for k, (a, b) in TREND_TODAY.items():
        s = sds.split(got[(k, MONEYNESS[0])])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: ATM must reproduce exit_ladder_study's Trend Rider today, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    W = 172
    pool = lambda keys, mm: sorted([x for k in keys for x in got[(k, mm)]], key=lambda x: x["when"])
    print("=" * W)
    print(" WHICH STRIKE - the same trades priced at the money and in / out of it. Per lot, after costs.  columns: trades,"
          " won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for keys, title in ((RULES, "SENSEX + MIDCAP SELECT (the tool's rules)"),
                        (TREND, "NIFTY + BANK NIFTY (the Trend Rider, 5-minute walk)")):
        print(" " + title)
        for mm in MONEYNESS:
            print(sds.line("   " + mm[0], pool(keys, mm)))
        for k in keys:
            print(f"   {k}")
            for mm in MONEYNESS:
                print(sds.line("     " + mm[0], got[(k, mm)]))
    print(" ALL FOUR")
    for mm in MONEYNESS:
        print(sds.line("   " + mm[0], pool(RULES + TREND, mm)))
    print("\n VERDICT - kept for a system only if more than ATM in BOTH periods")
    for keys, title in ((RULES, "Sensex + Midcap"), (TREND, "Nifty + Bank Nifty"), (RULES + TREND, "all four")):
        for mm in MONEYNESS[1:]:
            print("  " + sds.verdict(f"{mm[0]} on {title}", pool(keys, mm), pool(keys, MONEYNESS[0])).strip())
    return got


if __name__ == "__main__":
    main()
