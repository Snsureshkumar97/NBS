#!/usr/bin/env python3
"""
crypto_engine_votes_study.py — the crypto entry the user set on 6 Oct 2026, on 3 years of Exness REAL TICKS
================================================================================
The user: "for the crypto market votes i want to remove them and add EMA 20/50 vwap macd and adx gate above 25 only
these and room to run with risk reward back to 1:1" -> "One target at 1:1", "Switch now" (this is the backtest after).

Entries: the live engine exactly as configured now - Trend (EMA 20 / 50), MACD and VWAP, all three agreeing (RSI
dropped), ADX 25 or more, the room-to-run check, reward:risk at least 1:1 (tickets._reward_hold) and the spread check
(tickets._spread_hold) - bt.run() on Exness's own 15-minute candles. Exits walked tick by tick (cfd_tick_study.walk):
"plain 1R" is the new live exit (one target at the stop's distance, no step-up, the reversal exit, 24 hours at most);
2R and 5R (the exit before today) for context. Exness's spread in and out, its swap on buys, one position at a time,
20 minutes before re-entering the same way. $ per 1 BTC / per gold lot (100 oz).

For scale, the rules this replaced (their own studies, same costs): BTC RSI-2 87% +25,729 / +25,228 per BTC;
gold trend rule +108,722 / +47,774 per lot (in-sample / held-out).

    python3 crypto_engine_votes_study.py
"""
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SYMBOLS = {"BTCUSD": ("BTC", 1.0), "XAUUSD": ("GOLD", 100.0)}
VARIANTS = {"plain 1R (new live)": (("r", 1.0, 1.0), False, False, 0),
            "plain 2R": (("r", 2.0, 2.0), False, False, 0),
            "plain 5R (before today)": (("r", 5.0, 5.0), False, False, 0)}


def _entries(sym):
    """The live engine's entries under TODAY's crypto config, and its per-bar side (for the reversal exit). Cached."""
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    import reversal_exit_study as res
    import tickets
    key = SYMBOLS[sym][0]
    df = ed.load(sym)[["Open", "High", "Low", "Close", "Volume"]]
    tag = "tmv_" + "_".join(f"{k}{v}" for k, v in sorted(config.vote_overrides(key).items()) if k != "drop") + \
          "_drop" + "-".join(config.vote_overrides(key).get("drop", []))
    path = os.path.join(ed._dir(), f"votes_{sym}_{tag}_{len(df)}.pkl")
    if os.path.exists(path):
        return sym, path

    def gate(i, rec):
        return (tickets.TicketBook._reward_hold(cs._SELF, key, rec) is None
                and tickets.TicketBook._spread_hold(cs._SELF, rec) is None)

    entries = bt.run(key, df, gate=gate)["trades"]
    _, opp = res.bar_bias(key, df)
    with open(path, "wb") as fh:
        pickle.dump({"entries": entries, "opp": opp}, fh)
    return sym, path


def _job(args):
    sym, y, m, path = args
    import backtest_intraday as bt
    import cfd_target_study as cts
    import cfd_tick_study as t
    import config
    import exness_data as ed
    import indicators as ind
    key, mult = SYMBOLS[sym]
    full = ed.load(sym)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    with open(path, "rb") as fh:
        d = pickle.load(fh)
    t_lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC")
    t_hi = t_lo + pd.offsets.MonthBegin(1)
    entries = [tr for tr in d["entries"] if t_lo <= tr["when"].tz_convert("UTC") < t_hi]
    if not entries:
        return []
    parts = [p for p in (t._month_ticks(sym, y, m), t._month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
             if p is not None]
    if not parts:
        return []
    T = np.concatenate([p[0] for p in parts]); M = np.concatenate([p[1] for p in parts])
    S = np.concatenate([p[2] for p in parts])
    adx_arr = bt.precompute(df, key)["adx"].to_numpy()
    st_line = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    cl = df["Close"].to_numpy()
    sp_c = full["spread_close"].to_numpy()
    bar_start = df.index.tz_convert("UTC").asi8
    pos = {tt: n for n, tt in enumerate(df.index)}
    out = []
    for name, (how, ratchet, trail, be) in VARIANTS.items():
        for tr in entries:
            t1, t2 = cts.levels(tr, how)
            i = pos[tr["when"]]
            px, j, via, xs = t.walk(T, M, S, bar_start, cl, i, tr["side"], tr["entry"], tr["stop"], t1, t2,
                                    st_line if trail else None, d["opp"], ratchet=ratchet, trail=trail, be_bars=be)
            xs = sp_c[j] if not (xs == xs) else xs
            sign = 1 if tr["side"] == "CE" else -1
            net = (sign * (px - tr["entry"]) - sp_c[i] / 2 - xs / 2) * mult
            out.append({"variant": name, "when": df.index[i] + pd.Timedelta(minutes=15),
                        "exit_time": df.index[j] + pd.Timedelta(minutes=15), "side": tr["side"],
                        "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                        "exit_adx": float(adx_arr[j]), "net": net})
    return out


def main():
    import cfd_tick_study as t
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    print("Replaying the live engine (Trend EMA 20/50 + MACD + VWAP, ADX 25, room to run 1:1) on Exness data...", flush=True)
    with ProcessPoolExecutor(max_workers=2) as ex:
        paths = dict(ex.map(_entries, list(SYMBOLS)))
    months = ed.months_until(t.LAST)
    for sym in SYMBOLS:
        rows = []
        with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
            for got in ex.map(_job, [(sym, y, m, paths[sym]) for (y, m) in months]):
                rows.extend(got)
        key, mult = SYMBOLS[sym]
        long_sw, short_sw, triple = t.SWAP[sym]
        for r in rows:
            k = t.rollovers(r["when"], r["exit_time"], triple)
            r["net_noswap"] = r["net"]
            r["net"] = r["net"] + k * (long_sw if r["side"] == "CE" else short_sw)
        print("\n" + "=" * 132)
        print(f" EXNESS {sym} ON REAL TICKS - the new crypto entry; $ per {'1 BTC' if key == 'BTC' else 'gold lot'}, after spread and swap")
        print("=" * 132)
        print(f" {'exit':26s} {'IN-SAMPLE  n   win%   net after swap   PF    maxDD':>50s}   {'HELD-OUT  n   win%   net after swap   PF    maxDD':>50s}")
        for name in VARIANTS:
            vr = sorted((r for r in rows if r["variant"] == name), key=lambda r: r["when"])
            seq = cs.sequential(vr, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)
            p = cs.periods(seq)

            def c(rs):
                s = cs.stats(rs, "net")
                return f"{s['n']:>5} {s['win']:>5.1f}% {s['total']:>+12,.0f} {s['pf']:>6.2f} {s['dd']:>9,.0f}"
            print(f" {name:26s} {c(p['is']):>50s}   {c(p['oos']):>50s}")
    print("\n For scale - the rules this replaced: BTC RSI-2 87% +25,729 / +25,228 per BTC; gold trend rule +108,722 / "
          "+47,774 per lot (in-sample / held-out).")


if __name__ == "__main__":
    main()
