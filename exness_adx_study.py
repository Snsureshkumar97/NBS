#!/usr/bin/env python3
"""
exness_adx_study.py — which ADX gate works best on Exness BTCUSD and XAUUSD?
================================================================================
The user, 3 Oct 2026: "i want to see what adx gate will be better in exness ...
bitcoin and gold from exness" -> "A, do the research backtest on exness data".
Research only: no Exness account is connected, nothing changes live.

DATA: Exness's own public tick history (exness_data.py), as 15-minute mid-price
candles with the spread Exness actually quoted, bar by bar.

THE TRADES: the tool's own live BTC rule engine - backtest_intraday.run() with the
real build_recommendation() and tickets.py's crypto gates (_reward_hold at the
shipped crypto 1.0, _spread_hold), under the tool's own instrument keys ("BTC",
"GOLD") so each gets the crypto market's settings. The live exit (crypto_strategy_
study.walk_live: stop, T1 ratchet, Supertrend trail, 2h breakeven, T2, reversal,
96 bars), one position at a time with the live cooldown and its waiver. The
reversal exit reads the same ADX setting as the entry, so each setting gets its
own per-bar signal pass.

THE ADX GATE is swept through config.VOTE_OVERRIDES["crypto"]["adx"] - crypto
only, the Indian indices cannot be touched by it: 15, 20 (today), 25, 30, 35.

COSTS - EXNESS IS A CFD: no options, no option chain, no premium; a trade is a
buy or sell of the instrument itself. You buy at the ASK and sell at the BID, so
each trade pays half the quoted spread at entry (that bar's closing spread) and
half at exit (the exit bar's average spread for a stop/target hit inside the bar,
its closing spread for an exit at the close). The spread is the whole cost on an
Exness Standard account (no commission). NOT included: overnight SWAP - holds run
up to 24 hours, so many cross Exness's daily rollover; the swap is charged on top
and would make every result below somewhat worse.

UNITS: BTCUSD per 1 BTC (= 1 Exness lot); XAUUSD per 1 lot = 100 oz.

PERIODS: in-sample to 15 Aug 2025, held-out to 11 Sep 2026, and the live window
11 Sep - 30 Sep 2026 (Exness publishes a month after it ends).

    python3 exness_adx_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

SYMBOLS = {"BTCUSD": ("BTC", 1.0, "per 1 BTC"), "XAUUSD": ("GOLD", 100.0, "per lot (100 oz)")}
ADX_VALUES = (15, 20, 25, 30, 35)


def price_cfd(side, entry, exit_, spread_in, spread_out):
    """Net points per unit for one CFD trade: the move, less half the spread paid
    at entry and half at exit (buy at the ask, sell at the bid)."""
    sign = 1 if side == "CE" else -1
    return sign * (exit_ - entry) - spread_in / 2 - spread_out / 2


def _job(args):
    """One (symbol, ADX gate): the live engine's entries and its per-bar signal,
    in its own process, cached to disk."""
    sym, adx = args
    import pickle
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    import backtest_intraday as bt
    import reversal_exit_study as res
    import tickets

    key = SYMBOLS[sym][0]
    df = ed.load(sym)[["Open", "High", "Low", "Close", "Volume"]]
    path = os.path.join(ed._dir(), f"adx_{sym}_{adx}_{len(df)}.pkl")
    if os.path.exists(path):
        return sym, adx, path
    config.VOTE_OVERRIDES["crypto"] = {"adx": adx}

    def gate(i, rec):
        return (tickets.TicketBook._reward_hold(cs._SELF, key, rec) is None
                and tickets.TicketBook._spread_hold(cs._SELF, rec) is None)

    entries = bt.run(key, df, gate=gate)["trades"]
    _, opp = res.bar_bias(key, df)
    with open(path, "wb") as fh:
        pickle.dump({"entries": entries, "opp": opp}, fh)
    return sym, adx, path


def main():
    import pickle
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    import indicators as ind

    jobs = [(s, a) for s in SYMBOLS for a in ADX_VALUES]
    workers = max(1, min(len(jobs), (os.cpu_count() or 2) - 1))
    print(f"Replaying the live engine on Exness data: {len(jobs)} runs on {workers} processes...", flush=True)
    paths = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for sym, adx, path in ex.map(_job, jobs):
            paths[(sym, adx)] = path
            print(f"  done: {sym} ADX {adx}", flush=True)

    for sym, (key, mult, unit) in SYMBOLS.items():
        full = ed.load(sym)
        df = full[["Open", "High", "Low", "Close", "Volume"]]
        sp_close, sp_avg = full["spread_close"].to_numpy(), full["spread_avg"].to_numpy()
        pre = bt.precompute(df, key)
        adx_arr = pre["adx"].to_numpy()
        st_line = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()

        print("\n" + "=" * 140)
        print(f" EXNESS {sym} ({len(df):,} 15-min bars {df.index.min():%d %b %Y} - {df.index.max():%d %b %Y}),"
              f" $ {unit}, after Exness's real spread (swap NOT included). Median spread "
              f"{np.nanmedian(sp_avg):.2f} - that is {np.nanmedian(sp_avg) * mult:,.2f} $ {unit} per round trip.")
        print("=" * 140)
        results = {}
        for exit_name, kw in (("live exit", {}), ("no 2h breakeven", {"be_bars": 0})):
            print(f"\n {exit_name}")
            print(f" {'ADX gate':12s} {'IN-SAMPLE: n, gross, NET after spread, PF':>46s}   "
                  f"{'HELD-OUT':>40s}   {'LIVE WINDOW':>30s}")
            for adx in ADX_VALUES:
                with open(paths[(sym, adx)], "rb") as fh:
                    d = pickle.load(fh)
                rows = cs.run_live(df, None, d["entries"], d["opp"], st_line, adx_arr, **kw)
                for r in rows:
                    i, j = r["i"], r["j"]
                    out_sp = sp_close[j] if r["closed_via"] == "other" else sp_avg[j]
                    r["gross"] = ((1 if r["side"] == "CE" else -1) * (r["exit"] - r["entry"])) * mult
                    r["net"] = price_cfd(r["side"], r["entry"], r["exit"], sp_close[i], out_sp) * mult
                results[(exit_name, adx)] = rows
                p = cs.periods(rows)

                def c(rs_):
                    g, n_ = cs.stats(rs_, "gross"), cs.stats(rs_, "net")
                    return f"{n_['n']:>5} {g['total']:>+10,.0f} {n_['total']:>+10,.0f} PF{n_['pf']:>5.2f}"
                print(f" {'ADX >= ' + str(adx) + (' (today)' if adx == 20 else ''):12s} {c(p['is']):>46s}   "
                      f"{c(p['oos']):>40s}   {c(p['live']):>30s}")

        print("\n VERDICT - the best ADX gate picked on IN-SAMPLE NET only, then checked on held-out:")
        for exit_name in ("live exit", "no 2h breakeven"):
            best = max(ADX_VALUES, key=lambda a: cs.stats(cs.periods(results[(exit_name, a)])["is"], "net")["total"])
            p = cs.periods(results[(exit_name, best)])
            a, b = cs.stats(p["is"], "net"), cs.stats(p["oos"], "net")
            base = cs.periods(results[("live exit", 20)])
            ba, bb = cs.stats(base["is"], "net"), cs.stats(base["oos"], "net")
            verdict = ("PROFITABLE BOTH PERIODS" if a["total"] > 0 and b["total"] > 0
                       else "beats today both periods, still a loss" if a["total"] > ba["total"] and b["total"] > bb["total"]
                       else "not good enough")
            print(f"   {exit_name:16s} best ADX {best}: in-sample {a['total']:>+10,.0f} (PF {a['pf']:.2f})  "
                  f"held-out {b['total']:>+10,.0f} (PF {b['pf']:.2f})  vs today (ADX 20, live exit) "
                  f"{ba['total']:>+10,.0f} / {bb['total']:>+10,.0f}  -> {verdict}")
        rows = results[("live exit", 20)]
        if rows:
            gross = np.mean([r["gross"] for r in rows])
            cost = np.mean([r["gross"] - r["net"] for r in rows])
            hours = np.mean([(r["exit_time"] - r["when"]).total_seconds() / 3600 for r in rows])

            def over_rollover(r):
                # 21:00 UTC is a typical CFD rollover; Exness's exact swap time is an assumption here
                a_, b_ = r["when"].tz_convert("UTC"), r["exit_time"].tz_convert("UTC")
                roll = a_.normalize() + pd.Timedelta(hours=21)
                return b_ > (roll if roll > a_ else roll + pd.Timedelta(days=1))
            share = np.mean([over_rollover(r) for r in rows])
            print(f"   per trade at today's ADX 20: average move {gross:+,.2f} vs spread paid {cost:,.2f} "
                  f"({unit}); average hold {hours:.1f} h; {share:.0%} of trades are held over 21:00 UTC "
                  f"(a typical rollover) and would pay a swap not counted here")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
