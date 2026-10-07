#!/usr/bin/env python3
"""
index_candidates_study.py — is there an Indian index to trade instead of Bank Nifty? (3 years)
================================================================================
The user, 7 Oct 2026: "in indian market is there any other indices that dont make losses like banknifty but make
profits like nifty and sensex so that we can remove banknifty and add the other index".

THE CANDIDATES: every other Indian index with listed options - Fin Nifty (NIFTY FIN SERVICE), Midcap Select (NIFTY MID
SELECT), Nifty Next 50 and Bankex (BSE). Their 15-minute index candles for the same three years (18 Sep 2023 - 11 Sep
2026) were fetched read-only from Kite on 7 Oct 2026 into ~/trading-tool-logs/history.

HOW THEY TRADE TODAY (SEBI, Nov 2024: one weekly expiry per exchange): only Nifty (NSE) and Sensex (BSE) have weekly
options; Bank Nifty and every candidate are MONTHLY only. So the candidates are priced on monthly expiries throughout
(the last Thursday of the month, the last Tuesday from Sep 2025), and Bank Nifty is shown both on its real calendar and
monthly-only - the fair comparison for a swap made now. Lot sizes and strike steps are today's (Kite's instrument
list, 7 Oct 2026). Liquidity that day, ATM call + put traded on the nearest expiry, lots: Nifty ~7.5M, Sensex ~10.4M,
Bank Nifty ~110k, Midcap Select ~5.2k, Fin Nifty ~2.1k, Bankex 26, Nifty Next 50 0 - the last two cannot be traded
at the prices any backtest assumes.

Everything else exactly as the live system (stop_day_study.py's baseline): the live entry checks, the live exit (T1 ->
stop to T1, Supertrend trail, out at T2, 2-hour breakeven priced as it fills, day end), one position per index, the
cooldown waiver; Black-Scholes premiums at India VIX scaled by each index's own realised volatility against Nifty's;
costs as live. Slippage 0.25% a side (the studies' standard) and, for the thin candidates, 1% a side as well.

    python3 index_candidates_study.py
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NEW = {   # today's contract facts, Kite's instrument list 7 Oct 2026
    "FINNIFTY": {"kite_exchange": "NSE", "kite_tradingsymbol": "NIFTY FIN SERVICE", "strike_step": 50, "lot_size": 60},
    "MIDCPNIFTY": {"kite_exchange": "NSE", "kite_tradingsymbol": "NIFTY MID SELECT", "strike_step": 25, "lot_size": 120},
    "NIFTYNXT50": {"kite_exchange": "NSE", "kite_tradingsymbol": "NIFTY NEXT 50", "strike_step": 100, "lot_size": 25},
    "BANKEX": {"kite_exchange": "BSE", "kite_tradingsymbol": "BANKEX", "strike_step": 100, "lot_size": 30},
}
NAMES = {"NIFTY": "Nifty", "SENSEX": "Sensex", "BANKNIFTY": "Bank Nifty", "FINNIFTY": "Fin Nifty",
         "MIDCPNIFTY": "Midcap Select", "NIFTYNXT50": "Nifty Next 50", "BANKEX": "Bankex"}
JOBS = [("NIFTY", False, 0.0025), ("SENSEX", False, 0.0025), ("BANKNIFTY", False, 0.0025), ("BANKNIFTY", True, 0.0025),
        ("FINNIFTY", True, 0.0025), ("MIDCPNIFTY", True, 0.0025), ("NIFTYNXT50", True, 0.0025), ("BANKEX", True, 0.0025),
        ("FINNIFTY", True, 0.005), ("MIDCPNIFTY", True, 0.005), ("MIDCPNIFTY", True, 0.0075), ("BANKNIFTY", True, 0.005),
        ("FINNIFTY", True, 0.01), ("MIDCPNIFTY", True, 0.01), ("BANKNIFTY", True, 0.01)]


def _monthly(d):
    """The monthly expiry on or after d: the last Thursday of the month, the last Tuesday from Sep 2025."""
    import pro_study as ps
    for k in range(0, 3):
        y, m = d.year + (d.month + k - 1) // 12, (d.month + k - 1) % 12 + 1
        e = ps._last_weekday(y, m, 1 if dt.date(y, m, 1) >= dt.date(2025, 9, 1) else 3)
        if e >= d:
            return e
    return d


def _job(job):
    key, monthly, slip = job
    import config
    for k, v in NEW.items():
        config.INSTRUMENTS.setdefault(k, dict(v, market="nse_index", yahoo_ticker=None, nse_symbol=None,
                                              has_free_option_chain=False))
    import backtest_intraday as bt
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    import indicators as ind
    ps.SLIP = slip
    if monthly:
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: _monthly(d) if k == key else real(k, d)
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


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    print(f"Replaying the live system on {len(JOBS)} index / expiry / slippage combinations, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, JOBS))
    base = {"NIFTY": (154936, 30386), "SENSEX": (110280, 100217), "BANKNIFTY": (172293, -5376)}
    for k, (a, b) in base.items():
        s = sds.split(got[(k, False, 0.0025)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k} must reproduce today's figures: {s['is']['total']:.0f} / {s['oos']['total']:.0f}"

    print("=" * 172)
    print(" EACH INDEX ON ITS OWN, per lot, after costs - the live system unchanged.  columns: trades, won, total, profit "
          "factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for job in JOBS:
        key, monthly, slip = job
        label = (f"{NAMES[key]}" + ("" if not monthly else " (monthly)" if key == "BANKNIFTY" else "")
                 + ("" if monthly or key == "BANKNIFTY" else " (weekly)") + (f", {100 * slip:g}% slippage" if slip > 0.0025 else ""))
        if job == ("BANKNIFTY", False, 0.0025):
            label = "Bank Nifty (its real calendar) - TODAY"
        if job == ("FINNIFTY", True, 0.0025):
            print("-" * 172)
        print(sds.line(label, got[job]))
        if job == ("NIFTY", False, 0.0025) or job == ("SENSEX", False, 0.0025):
            continue

    print("\n PER TRADE (avg Rs per lot, after costs): in-sample / held-out")
    for job in JOBS:
        s = sds.split(got[job])
        key, monthly, slip = job
        print(f"   {NAMES[key]:15s} {'monthly' if monthly else 'real calendar':14s} slip {100 * slip:4.2f}%   "
              f"{s['is']['avg'] if s['is']['n'] else 0:>+7,.0f} / {s['oos']['avg'] if s['oos']['n'] else 0:>+7,.0f}"
              f"   ({s['is']['n']} / {s['oos']['n']} trades)")

    print("\n THE SWAP: Nifty + Sensex + the third index (per lot, totals added - each index runs on its own)")
    ns = [sds.split(got[("NIFTY", False, 0.0025)]), sds.split(got[("SENSEX", False, 0.0025)])]
    for job in [("BANKNIFTY", False, 0.0025), ("BANKNIFTY", True, 0.0025), ("FINNIFTY", True, 0.0025),
                ("FINNIFTY", True, 0.005), ("FINNIFTY", True, 0.01), ("MIDCPNIFTY", True, 0.0025), ("MIDCPNIFTY", True, 0.005),
                ("MIDCPNIFTY", True, 0.0075), ("MIDCPNIFTY", True, 0.01), None]:
        third = sds.split(got[job]) if job else None
        a = sum(x["is"]["total"] for x in ns) + (third["is"]["total"] if third else 0)
        b = sum(x["oos"]["total"] for x in ns) + (third["oos"]["total"] if third else 0)
        name = ("+ " + NAMES[job[0]] + (" (monthly)" if job[1] else "") + (f", {100 * job[2]:g}% slip" if job[2] > 0.0025 else "")) if job else "Nifty + Sensex only (drop Bank Nifty)"
        print(f"   {name:44s} in-sample {a:>+10,.0f}   held-out {b:>+10,.0f}")


if __name__ == "__main__":
    main()
