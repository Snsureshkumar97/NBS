#!/usr/bin/env python3
"""
cfd_momentum_study.py — RIDE the BTC trend while it runs, get out as it fades (Exness, 15-minute)
================================================================================
The user, 4 Oct 2026: "i want to have like chance the trend while its running but exit before the trend fades
and get the profit and come out". Not the RSI-2 rule's shape (buy a dip, one close target): enter WITH a
running trend and let an exit that FOLLOWS it decide when to leave. 12 textbook combinations, fixed before
any result was seen - no tuning:

ENTRIES (each needs ADX >= 25 - a trend that is running), on a 15-minute close:
  breakout     the close beyond the last 20 candles' high (buy) / low (sell)
  ema momentum EMA20 > EMA50, the close above EMA20, up over 3 hours (ROC12), ADX rising (sells mirrored)
  st flip      Supertrend (10, 2.5) turns up (buy) / down (sell)
EXITS (each with a protective stop at 3 x ATR from the entry; out after 24 hours at the latest, as live):
  trail        a chandelier trailing stop: 3 x ATR under the highest high since entry (above the lowest low)
  ema20        out at the close when a candle closes back through EMA20
  fade         out at the close when ADX turns down (below its value 3 candles ago) or MACD's histogram
               turns against the trade
  st trail     the Supertrend line as the trailing stop; out at the close if it flips

ON EXNESS'S OWN 15-MINUTE CANDLES (built from its real ticks): with no target, a candle holds only ONE level
(the stop), so its low/high answer exactly whether it was touched - filled at the stop, or at the open if
price jumped through it. Indicator exits at the close. A stop moved at a close counts from the next candle
(nothing looks ahead). Costs as the demo account: Exness's spread, at least $10 (half in, half out), its
swap on buys; one position at a time, 20 minutes after an exit.

THE TEST: in-sample to 15 Aug 2025, held-out after. A combination counts only if profitable after costs in
BOTH periods AND its held-out beats 1 - 0.05/12 of coin flips (the same entries, a random direction, the
same exit). Beside them, for scale: the live RSI-2 rule (cfd_monthly_live_rules.py: +25,729 / +25,228).

    python3 cfd_momentum_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

FLOOR = 10.0
STOP_ATR = 3.0
HOLD_BARS = 96
COOLDOWN_NS = 20 * 60 * 10 ** 9
BAR_NS = 15 * 60 * 10 ** 9
N_FLIPS = 300
K = 12


def build():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import exness_data as ed
    import indicators as ind
    full = ed.load("BTCUSD")
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    o, h, l, c = (df[k] for k in ("Open", "High", "Low", "Close"))
    a = cfd_rules.compute(df)
    adx = pd.Series(a["adx_value"])
    ema20, ema50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = (macd - macd.ewm(span=9, adjust=False).mean()).to_numpy()
    st_line, st_dir = ind.supertrend(df, 10, 2.5)
    st_line, st_dir = st_line.to_numpy(), st_dir.to_numpy().astype(int)
    strong = (adx >= 25).to_numpy()
    rising = (adx > adx.shift(3)).to_numpy()
    hh, ll = h.rolling(20).max().shift().to_numpy(), l.rolling(20).min().shift().to_numpy()
    cv = c.to_numpy()
    roc = (c - c.shift(12)).to_numpy()
    e20, e50 = ema20.to_numpy(), ema50.to_numpy()
    entries = {
        "breakout": np.where(strong & (cv > hh), 1, np.where(strong & (cv < ll), -1, 0)),
        "ema momentum": np.where(strong & rising & (e20 > e50) & (cv > e20) & (roc > 0), 1,
                                 np.where(strong & rising & (e20 < e50) & (cv < e20) & (roc < 0), -1, 0)),
        "st flip": np.where(strong & (st_dir == 1) & (np.roll(st_dir, 1) == -1), 1,
                            np.where(strong & (st_dir == -1) & (np.roll(st_dir, 1) == 1), -1, 0)),
    }
    for k_ in entries:
        entries[k_][:250] = 0
    O = np.load(os.path.join(ed._dir(), "outcomes_BTCUSD.npz"))
    entry_ns = full.index.tz_convert("UTC").asi8 + BAR_NS
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(entry_ns[0], entry_ns[-1] + 2 * 86400 * 10 ** 9, triple)
    return {"o": o.to_numpy(), "h": h.to_numpy(), "l": l.to_numpy(), "c": cv, "atr": a["atr"], "adx": adx.to_numpy(),
            "hist": hist, "e20": e20, "st_line": st_line, "st_dir": st_dir, "entries": entries, "ok": O["ok"],
            "eff": np.maximum(full["spread_close"].to_numpy(), FLOOR), "entry_ns": entry_ns, "long_sw": long_sw,
            "rolls": rolls, "cumw": cumw, "split": vs.SPLIT, "stats": vs.stats}


def simulate(D, side_arr, exit_kind):
    """One position at a time. -> list of (entry_ns, side, $ per BTC, bars held, how)."""
    o, h, l, c, atr, adx, hist, e20 = D["o"], D["h"], D["l"], D["c"], D["atr"], D["adx"], D["hist"], D["e20"]
    st_line, st_dir, eff, ens = D["st_line"], D["st_dir"], D["eff"], D["entry_ns"]
    n = len(c)
    out, free = [], -1
    for i in np.flatnonzero((side_arr != 0) & D["ok"]):
        if ens[i] < free or i + 1 >= n:
            continue
        s, e = int(side_arr[i]), c[i]
        stop = e - s * STOP_ATR * atr[i]
        best = e
        how, px, jx = "time", None, None
        for j in range(i + 1, min(i + 1 + HOLD_BARS, n)):
            if s > 0 and l[j] <= stop:
                px, jx, how = min(stop, o[j]), j, "stop"
                break
            if s < 0 and h[j] >= stop:
                px, jx, how = max(stop, o[j]), j, "stop"
                break
            if exit_kind == "ema20" and s * (c[j] - e20[j]) < 0:
                px, jx, how = c[j], j, "signal"
                break
            if exit_kind == "fade" and j >= 3 and (adx[j] < adx[j - 3] or s * hist[j] < 0):
                px, jx, how = c[j], j, "signal"
                break
            if exit_kind == "st trail" and st_dir[j] != s:
                px, jx, how = c[j], j, "signal"
                break
            if exit_kind == "trail":
                best = max(best, h[j]) if s > 0 else min(best, l[j])
                lvl = best - s * STOP_ATR * atr[j]
                stop = max(stop, lvl) if s > 0 else min(stop, lvl)
            if exit_kind == "st trail" and st_dir[j] == s:
                stop = max(stop, st_line[j]) if s > 0 else min(stop, st_line[j])
        if px is None:
            jx = min(i + HOLD_BARS, n - 1)
            px = c[jx]
        t1 = ens[jx]                                            # the exit candle's close (a stop: no later than it)
        nights = (D["cumw"][np.searchsorted(D["rolls"], t1, side="right") - 1]
                  - D["cumw"][np.searchsorted(D["rolls"], ens[i], side="right") - 1])
        usd = s * (px - e) - eff[i] / 2 - eff[jx] / 2 + (nights * D["long_sw"] if s > 0 else 0.0)
        out.append((ens[i], s, usd, jx - i, how))
        free = t1 + COOLDOWN_NS
    return out


def summary(D, trades, period):
    ins = np.array([t[0] < D["split"] for t in trades], dtype=bool)
    m = ins if period == "is" else ~ins
    x = np.array([t[2] for t in trades])[m] if trades else np.array([])
    s = D["stats"](x)
    held = np.array([t[3] for t in trades])[m] if trades else np.array([])
    w, lo = x[x > 0], x[x <= 0]
    s.update(avg_win=float(w.mean()) if len(w) else 0.0, avg_loss=float(lo.mean()) if len(lo) else 0.0,
             hold_h=float(np.median(held)) / 4 if len(held) else 0.0)
    return s


def main():
    D = build()
    rng = np.random.default_rng(20261004)
    print("BTC on Exness, 15-minute, $ per BTC; in-sample to 15 Aug 2025, held-out after. "
          "For scale, the live RSI-2 rule: +25,729 / +25,228 (87% won).\n")
    print(f"  {'entry + exit':26s} {'IN-SAMPLE   n  win%   avgW   avgL      net   PF  hold':>52s}   "
          f"{'HELD-OUT   n  win%   avgW   avgL      net   PF  hold':>52s}   {'coin p':>7s}")
    res = []
    for en, arr in D["entries"].items():
        for ex in ("trail", "ema20", "fade", "st trail"):
            tr = simulate(D, arr, ex)
            si, so = summary(D, tr, "is"), summary(D, tr, "oos")
            p, keep = None, False
            if si["net"] > 0 and so["net"] > 0:
                ho_idx = np.flatnonzero((arr != 0) & D["ok"] & (D["entry_ns"] >= D["split"]))
                flips = []
                for _ in range(N_FLIPS):
                    rs = np.zeros_like(arr)
                    rs[ho_idx] = rng.choice([-1, 1], len(ho_idx))
                    flips.append(sum(t[2] for t in simulate(D, rs, ex)))
                p = (1 + np.sum(np.array(flips) >= so["net"])) / (1 + N_FLIPS)
                keep = p <= 0.05 / K
            cell = lambda s: (f"{s['n']:>5} {100 * s['win']:>4.0f}% {s['avg_win']:>+6.0f} {s['avg_loss']:>+6.0f} "
                              f"{s['net']:>+9,.0f} {s['pf']:>4.2f} {s['hold_h']:>4.1f}h")
            print(f"  {en + ' + ' + ex:26s} {cell(si):>52s}   {cell(so):>52s}   "
                  f"{'' if p is None else f'{p:>7.3f}'}{'  KEEP' if keep else ''}", flush=True)
            res.append((en, ex, si, so, p, keep))
    both = [r for r in res if r[2]["net"] > 0 and r[3]["net"] > 0]
    kept = [r for r in res if r[5]]
    print(f"\n  profitable in both periods: {len(both)} of {K}" + (" - " + "; ".join(f"{r[0]} + {r[1]}" for r in both) if both else ""))
    print(f"  pass the strict test (coin flips <= {0.05 / K:.4f}): {len(kept)}" + (" - " + "; ".join(f"{r[0]} + {r[1]}" for r in kept) if kept else ""))


if __name__ == "__main__":
    main()
