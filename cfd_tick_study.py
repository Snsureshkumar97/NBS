#!/usr/bin/env python3
"""
cfd_tick_study.py — the Exness BTC / gold exits, judged on Exness's REAL TICKS (3 years)
================================================================================
Why: cfd_tick_check.py (3 Oct 2026) replayed June-August 2026 on Exness's own ticks and
found the candle backtests used for every Exness decision so far too optimistic. The
candle walker (crypto_strategy_study.walk_live) lets a trade touch T1 - which moves the
stop up to T1 - and then reach the T2 exit in the SAME 15-minute candle; on the ticks
price very often came back to T1 first and stopped the trade there (207 of 245 BTC
"target" exits at T1 0.3R / T2 0.6R). The closer the targets, the bigger the error; the
live targets have it too. Candle ordering cannot settle it; the ticks can.

So every exit here is walked tick by tick (cfd_tick_check.walk_ticks): the stop fills at
the first tick through it (its own price), T1 moves the stop, the target is a resting
limit, and at each 15-minute close the breakeven / Supertrend trail / close-beyond-stop /
reversal exits, exactly the live order. Entries are the live engine's (exness_adx_study's
cache: BTC at ADX 20, gold at its live 25). Exness's spread is paid in (entry bar's close)
and out (the exit tick's own spread). One position at a time, as live.

Exits compared:
  today          reach targets, T1 moves the stop, Supertrend trail, exit at T2, no 2h breakeven (live)
  today + 2h BE  the same with the 2-hour breakeven the Indian indices keep
  R a/b          T1 = a x risk, T2 = b x risk, otherwise as today
  plain bR       no T1 step and no trail: the stop, ONE target at b x risk, the reversal exit, 24h

SWAP: Exness charges an overnight swap on BUYS only on this account type (its own symbol
specification, read 3 Oct 2026: BTCUSDm long -1,902.3 points = -$19.02 a lot a night, short 0,
triple on Friday; XAUUSDm long -537.6 points = -$53.76 a lot a night, short 0, triple on
Wednesday). Charged here at each 21:00 UTC rollover a trade is held through, Monday to Friday,
three times on the triple day - TODAY's rates applied to all three years (an estimate: rates
move). Both results are printed: before and after swap.

Periods: in-sample to 15 Aug 2025, held-out to 11 Sep 2026 (ticks run to 31 Aug 2026).

    python3 cfd_tick_study.py
"""
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

SYMBOLS = {"BTCUSD": ("BTC", 1.0, 20), "XAUUSD": ("GOLD", 100.0, 25)}
LAST = (2026, 8)
# $ per lot per night on a BUY, on a SELL, and the weekday charged three times (Mon = 0)
SWAP = {"BTCUSD": (-19.023, 0.0, 4), "XAUUSD": (-53.76, 0.0, 2)}


def rollovers(t0, t1, triple_wd):
    """Weighted count of 21:00 UTC rollovers in (t0, t1], Mon-Fri, x3 on the triple day."""
    a, b = t0.tz_convert("UTC"), t1.tz_convert("UTC")
    roll = a.normalize() + pd.Timedelta(hours=21)
    if roll <= a:
        roll += pd.Timedelta(days=1)
    n = 0
    while roll <= b:
        wd = roll.weekday()
        if wd < 5:
            n += 3 if wd == triple_wd else 1
        roll += pd.Timedelta(days=1)
    return n
# name -> (levels "how" for cfd_target_study.levels, ratchet, trail, be_bars)
VARIANTS = {
    "today":          (None, True, True, 0),
    "today + 2h BE":  (None, True, True, 8),
    "R 0.3/0.6":      (("r", 0.3, 0.6), True, True, 0),
    "R 0.5/1":        (("r", 0.5, 1.0), True, True, 0),
    "R 1/2":          (("r", 1.0, 2.0), True, True, 0),
    "R 1.5/3":        (("r", 1.5, 3.0), True, True, 0),
    "plain 1R":       (("r", 1.0, 1.0), False, False, 0),
    "plain 1.5R":     (("r", 1.5, 1.5), False, False, 0),
    "plain 2R":       (("r", 2.0, 2.0), False, False, 0),
    "plain 3R":       (("r", 3.0, 3.0), False, False, 0),
}
BAR_NS = 15 * 60 * 10 ** 9


def first(mask):
    k = np.flatnonzero(mask)
    return int(k[0]) if len(k) else None


def walk(T, M, S, bar_start, cl, i, side, entry, stop, t1, t2, st_line, opp,
         ratchet=True, trail=True, be_bars=0, max_bars=96):
    """(exit_price, exit_bar, via, exit_spread or nan) - the exit on ticks, the live order."""
    ce = side == "CE"
    t1_done = be_done = False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        a, b = np.searchsorted(T, bar_start[j]), np.searchsorted(T, bar_start[j] + BAR_NS)
        mid = M[a:b]
        k0 = 0
        while k0 < len(mid):
            seg = mid[k0:]
            s_hit = first(seg <= stop) if ce else first(seg >= stop)
            if ratchet and not t1_done and t1 is not None:
                u_hit = first(seg >= t1) if ce else first(seg <= t1)
                if s_hit is not None and (u_hit is None or s_hit < u_hit):
                    return seg[s_hit], j, "stop", S[a + k0 + s_hit]
                if u_hit is None:
                    break
                t1_done = True
                stop = max(stop, t1) if ce else min(stop, t1)
                k0 += u_hit
                continue
            g_hit = (first(seg >= t2) if ce else first(seg <= t2)) if t2 is not None else None
            if s_hit is not None and (g_hit is None or s_hit < g_hit):
                return seg[s_hit], j, "stop", S[a + k0 + s_hit]
            if g_hit is not None:
                return t2, j, "target", S[a + k0 + g_hit]
            break
        if be_bars and not t1_done and not be_done and j - i >= be_bars:
            stop = max(stop, entry) if ce else min(stop, entry)
            be_done = True
        if trail and t1_done and st_line is not None:
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


def _month_ticks(sym, y, m):
    import exness_data as ed
    npz = os.path.join(ed._dir(), f"{sym}_{y}_{m:02d}_ticks.npz")
    if not os.path.exists(npz):
        return None
    d = np.load(npz)
    return d["ts"], d["mid"], d["spr"]


def _job(args):
    """One symbol-month: every variant for the entries made in that month (ticks of the
    month and the next, for trades that run past its end)."""
    sym, y, m = args[:3]
    variants = args[3] if len(args) > 3 else VARIANTS        # handed in: a worker re-imports this module
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import backtest_intraday as bt
    import cfd_target_study as cts
    import config
    import exness_data as ed
    import indicators as ind
    key, mult, gate = SYMBOLS[sym]
    full = ed.load(sym)
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    with open(os.path.join(ed._dir(), f"adx_{sym}_{gate}_{len(df)}.pkl"), "rb") as fh:
        d = pickle.load(fh)
    t_lo = pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC")
    t_hi = t_lo + pd.offsets.MonthBegin(1)
    entries = [tr for tr in d["entries"] if t_lo <= tr["when"].tz_convert("UTC") < t_hi]
    if not entries:
        return []
    parts = [p for p in (_month_ticks(sym, y, m), _month_ticks(sym, *((y + 1, 1) if m == 12 else (y, m + 1))))
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
    pos = {t: n for n, t in enumerate(df.index)}
    out = []
    for name, (how, ratchet, trail, be) in variants.items():
        for tr in entries:
            t1, t2 = cts.levels(tr, how)
            i = pos[tr["when"]]
            px, j, via, xs = walk(T, M, S, bar_start, cl, i, tr["side"], tr["entry"], tr["stop"], t1, t2,
                                  st_line if trail else None, d["opp"], ratchet=ratchet, trail=trail, be_bars=be)
            xs = sp_c[j] if not (xs == xs) else xs
            sign = 1 if tr["side"] == "CE" else -1
            net = (sign * (px - tr["entry"]) - sp_c[i] / 2 - xs / 2) * mult
            out.append({"variant": name, "when": df.index[i] + pd.Timedelta(minutes=15),
                        "exit_time": df.index[j] + pd.Timedelta(minutes=15), "side": tr["side"],
                        "entry": tr["entry"], "exit": px, "i": i, "j": j,
                        "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                        "exit_adx": float(adx_arr[j]), "net": net})
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config
    import crypto_strategy_study as cs
    import exness_data as ed
    months = ed.months_until(LAST)
    for sym in SYMBOLS:
        rows = []
        with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
            for got in ex.map(_job, [(sym, y, m, VARIANTS) for (y, m) in months]):
                rows.extend(got)
        key, mult, gate = SYMBOLS[sym]
        print("\n" + "=" * 128)
        print(f" EXNESS {sym} ON REAL TICKS - live entries (ADX {gate}), $ per {'1 BTC' if key == 'BTC' else 'gold lot'}, "
              f"after the spread actually quoted")
        print("=" * 128)
        print(f" {'exit':15s} {'IN-SAMPLE  n   win%   net after swap  PF  maxDD':>46s}   {'HELD-OUT  n   win%   net after swap  PF  maxDD':>46s}")
        long_sw, short_sw, triple = SWAP[sym]
        for r in rows:
            k = rollovers(r["when"], r["exit_time"], triple)
            r["net_noswap"] = r["net"]
            r["net"] = r["net"] + k * (long_sw if r["side"] == "CE" else short_sw)
        res = {}
        for name in VARIANTS:
            vr = sorted((r for r in rows if r["variant"] == name), key=lambda r: r["when"])
            seq = cs.sequential(vr, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)
            p = cs.periods(seq)
            res[name] = p

            def c(rs):
                s = cs.stats(rs, "net")
                return f"{s['n']:>5} {s['win']:>5.1f}% {s['total']:>+11,.0f} {s['pf']:>5.2f} {s['dd']:>9,.0f}"
            ns_is, ns_oos = cs.stats(p["is"], "net_noswap")["total"], cs.stats(p["oos"], "net_noswap")["total"]
            print(f" {name:15s} {c(p['is']):>46s}   {c(p['oos']):>46s}   (before swap {ns_is:+,.0f} / {ns_oos:+,.0f})")
        b = res["today"]
        bi, bo = cs.stats(b["is"], "net")["total"], cs.stats(b["oos"], "net")["total"]
        print("\n vs today (KEEP = better net in BOTH periods); 'profit both' = positive in both:")
        for name in VARIANTS:
            p = res[name]
            a_i, a_o = cs.stats(p["is"], "net")["total"], cs.stats(p["oos"], "net")["total"]
            tag = ("KEEP" if (a_i > bi and a_o > bo) else "drop") if name != "today" else "----"
            print(f"   {name:15s} {a_i - bi:>+11,.0f}  {a_o - bo:>+11,.0f}  {tag}  {'profit both' if a_i > 0 and a_o > 0 else ''}")


if __name__ == "__main__":
    main()
