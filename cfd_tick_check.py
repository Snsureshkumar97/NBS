#!/usr/bin/env python3
"""
cfd_tick_check.py — do the closer CFD targets survive Exness's real ticks?
================================================================================
cfd_target_study.py / exness_strategy_shootout.py found TradePicker's own engine with
T1 = 0.3 x risk and the exit (T2) = 0.6 x risk the best on Exness BTC and gold, both
periods. At those distances the levels sit INSIDE one 15-minute candle's range (BTC T2
~$190 against a ~$158 bar; gold ~$5 against ~$8), so a candle backtest cannot see
whether the target or the stop came first inside a bar - it assumed the stop (the
pessimistic order). This replays the SAME trades on Exness's own ticks, in order:

  * every tick: the stop (filled at the first tick that crosses it - its own price,
    not the stop's, so a jump through the stop costs what it really cost), T1 (moves
    the stop up to T1), the T2 exit (a resting limit: filled at T2);
  * every 15-minute close: the Supertrend trail after T1 (and a close already beyond
    the moved stop exits there), the reversal exit, the 96-bar limit;
  * the spread actually quoted on the entry bar's close and on the exit tick.

Held-out months June - August 2026 (Exness's public tick archive, ~310 MB, downloaded
once and kept under the study cache). Same entries as the studies (the live engine:
BTC at ADX 20, gold at its live 25), one position at a time as live.

    python3 cfd_tick_check.py
"""
import os
import pickle
import sys
import urllib.request

import numpy as np
import pandas as pd

MONTHS = ((2026, 6), (2026, 7), (2026, 8))
SYMBOLS = {"BTCUSD": ("BTC", 1.0, 20), "XAUUSD": ("GOLD", 100.0, 25)}
VARIANTS = (("today", None), ("R 0.3/0.6", ("r", 0.3, 0.6)), ("R 0.2/0.4", ("r", 0.2, 0.4)),
            ("R 0.5/1", ("r", 0.5, 1.0)))
BAR_NS = 15 * 60 * 10 ** 9


def ticks(sym):
    """(ts ns UTC, mid, spread) for MONTHS, cached as one .npz per month."""
    import exness_data as ed
    out = []
    for y, m in MONTHS:
        npz = os.path.join(ed._dir(), f"{sym}_{y}_{m:02d}_ticks.npz")
        if not os.path.exists(npz):
            z = npz.replace("_ticks.npz", "_ticks.zip")
            if not os.path.exists(z):
                req = urllib.request.Request(ed.URL.format(s=sym, y=y, m=m), headers={"User-Agent": "nbs-exness-study"})
                with urllib.request.urlopen(req, timeout=600) as r, open(z + ".part", "wb") as fh:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        fh.write(b)
                os.replace(z + ".part", z)
            ts, mid, spr = [], [], []
            for ch in pd.read_csv(z, usecols=["Timestamp", "Bid", "Ask"], chunksize=ed.CHUNK,
                                  dtype={"Bid": "float64", "Ask": "float64"}):
                ch = ch[(ch["Bid"] > 0) & (ch["Ask"] >= ch["Bid"])]
                t = pd.to_datetime(ch["Timestamp"], format="%Y-%m-%d %H:%M:%S.%fZ", utc=True)
                ts.append(t.astype("int64").to_numpy())
                mid.append(((ch["Bid"] + ch["Ask"]) / 2).to_numpy())
                spr.append((ch["Ask"] - ch["Bid"]).to_numpy())
            np.savez(npz, ts=np.concatenate(ts), mid=np.concatenate(mid), spr=np.concatenate(spr))
            os.remove(z)
        d = np.load(npz)
        out.append((d["ts"], d["mid"], d["spr"]))
    ts = np.concatenate([o[0] for o in out])
    order = np.argsort(ts, kind="stable")
    return ts[order], np.concatenate([o[1] for o in out])[order], np.concatenate([o[2] for o in out])[order]


def first(mask):
    k = np.flatnonzero(mask)
    return int(k[0]) if len(k) else None


def walk_ticks(T, M, S, bar_start, bar_close, cl, i, side, entry, stop, t1, t2, st_line, opp, max_bars=96):
    """(exit_price, exit_bar, via, exit_spread) - the live exit on ticks."""
    ce = side == "CE"
    t1_done = False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        a, b = np.searchsorted(T, bar_start[j]), np.searchsorted(T, bar_start[j] + BAR_NS)
        mid = M[a:b]
        k0 = 0
        while k0 < len(mid):
            seg = mid[k0:]
            s_hit = first(seg <= stop) if ce else first(seg >= stop)
            if not t1_done and t1 is not None:
                u_hit = first(seg >= t1) if ce else first(seg <= t1)
                if s_hit is not None and (u_hit is None or s_hit < u_hit):
                    return seg[s_hit], j, "stop", S[a + k0 + s_hit]
                if u_hit is None:
                    break
                t1_done = True
                stop = max(stop, t1) if ce else min(stop, t1)
                k0 += u_hit                     # from the T1 tick on, with the moved stop
                continue
            g_hit = (first(seg >= t2) if ce else first(seg <= t2)) if t2 is not None else None
            if s_hit is not None and (g_hit is None or s_hit < g_hit):
                return seg[s_hit], j, "stop", S[a + k0 + s_hit]
            if g_hit is not None:
                return t2, j, "target", S[a + k0 + g_hit]
            break
        # the bar's close: the trail after T1, a close beyond the moved stop, the reversal
        if t1_done and st_line is not None:
            s_ = st_line[j]
            if s_ == s_:
                stop = max(stop, s_) if ce else min(stop, s_)
        last = mid[-1] if len(mid) else cl[j]
        if (last <= stop) if ce else (last >= stop):
            return last, j, "stop", (S[b - 1] if b > a else np.nan)
        if opp is not None and opp[j] is not None and opp[j] != side:
            return last, j, "other", (S[b - 1] if b > a else np.nan)
    j = min(i + max_bars, n - 1)
    return cl[j], j, "other", np.nan


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import backtest_intraday as bt
    import cfd_target_study as cts
    import config
    import crypto_strategy_study as cs
    import exness_adx_study as ex
    import exness_data as ed
    import indicators as ind

    for sym, (key, mult, adx_gate) in SYMBOLS.items():
        full = ed.load(sym)
        df = full[["Open", "High", "Low", "Close", "Volume"]]
        sp_c, sp_a = full["spread_close"].to_numpy(), full["spread_avg"].to_numpy()
        with open(os.path.join(ed._dir(), f"adx_{sym}_{adx_gate}_{len(df)}.pkl"), "rb") as fh:
            d = pickle.load(fh)
        adx_arr = bt.precompute(df, key)["adx"].to_numpy()
        st_line = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
        cl = df["Close"].to_numpy()
        bar_start = df.index.tz_convert("UTC").asi8
        lo_t = pd.Timestamp(f"{MONTHS[0][0]}-{MONTHS[0][1]:02d}-01", tz="UTC")
        hi_t = pd.Timestamp(f"{MONTHS[-1][0]}-{MONTHS[-1][1]:02d}-01", tz="UTC") + pd.offsets.MonthBegin(1)
        win = [tr for tr in d["entries"] if lo_t <= tr["when"].tz_convert("UTC") < hi_t - pd.Timedelta(days=1)]
        print(f"\n{sym}: loading ticks ...", flush=True)
        T, M, S = ticks(sym)
        pos = {t: n for n, t in enumerate(df.index)}
        print(f"{sym}: {len(T):,} ticks, {len(win)} entries in {MONTHS[0][0]}-{MONTHS[0][1]:02d}.."
              f"{MONTHS[-1][0]}-{MONTHS[-1][1]:02d}")
        print(f" {'targets':12s} {'CANDLES  n  win%   net      PF':>36s}   {'TICKS  n  win%   net      PF':>36s}   "
              f"{'same trades: candle vs tick result':>36s}")
        for name, how in VARIANTS:
            entries = [dict(tr, **dict(zip(("t1", "t2"), cts.levels(tr, how)))) for tr in win]
            bar_rows = cs.run_live(df, None, entries, d["opp"], st_line, adx_arr, be_bars=0, exit_key="t2")
            for r in bar_rows:
                out_sp = sp_c[r["j"]] if r["closed_via"] == "other" else sp_a[r["j"]]
                r["net"] = ex.price_cfd(r["side"], r["entry"], r["exit"], sp_c[r["i"]], out_sp) * mult
            raw = []
            for tr in entries:
                i = pos[tr["when"]]
                px, j, via, xs = walk_ticks(T, M, S, bar_start, None, cl, i, tr["side"], tr["entry"], tr["stop"],
                                            tr["t1"], tr["t2"], st_line, d["opp"])
                xs = sp_c[j] if not (xs == xs) else xs
                raw.append({"when": df.index[i] + cs.BAR, "exit_time": df.index[j] + cs.BAR, "side": tr["side"],
                            "entry": tr["entry"], "exit": px, "i": i, "j": j, "closed_via": via,
                            "exit_adx": float(adx_arr[j]),
                            "net": ex.price_cfd(tr["side"], tr["entry"], px, sp_c[i], xs) * mult})
            tick_rows = cs.sequential(raw, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)
            b_, t_ = cs.stats(bar_rows, "net"), cs.stats(tick_rows, "net")
            common = {r["when"] for r in bar_rows} & {r["when"] for r in tick_rows}
            bm = {r["when"]: r["net"] for r in bar_rows}
            tm = {r["when"]: r["net"] for r in tick_rows}
            diff = np.mean([tm[w] - bm[w] for w in common]) if common else float("nan")
            print(f" {name:12s} {b_['n']:>5} {b_['win']:>5.1f}% {b_['total']:>+10,.0f} {b_['pf']:>5.2f}   "
                  f"{t_['n']:>5} {t_['win']:>5.1f}% {t_['total']:>+10,.0f} {t_['pf']:>5.2f}   "
                  f"{len(common):>5} trades, ticks {diff:+,.2f}/trade vs candles")


if __name__ == "__main__":
    main()
