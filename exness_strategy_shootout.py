#!/usr/bin/env python3
"""
exness_strategy_shootout.py — published BTC / gold strategies vs TradePicker, on Exness
================================================================================
The user, 3 Oct 2026: "use the best strategy that works in exness for these market
check everywhere online github everywhere you can look and use the best strategy for
entry stop loss and targets for best accuracy dont touch the indian market".

Research only - nothing here changes the live tool. Every strategy is ported from its
own published source, as published (its timeframe, entry, stop, take-profit schedule
and exit), then run on Exness's own 3-year 15-minute history (exness_data.py) with
Exness's real spread paid in and out, one position at a time:

  Flawless Victory v1/v2/v3  TradingView "15min BTC Machine Learning Strategy" (Pine source:
                             github.com/hasnocool/tradingview-pine-scripts). Long only. v1:
                             close < BB(20,1) lower and RSI14 > 42, exit close > upper and RSI
                             > 70, no stop. v2: BB(17,1), exit RSI > 76, SL 6.604% / TP 2.328%.
                             v3: BB(20,1) and MFI14 < 60, exit close > upper and RSI > 65 and
                             MFI > 64, SL 8.882% / TP 2.317% (MFI on Exness's tick volume).
  BbandRsi                   freqtrade-strategies (berlinguyinca). 1h, long: RSI14 < 30 and close
                             < BB(20,2) lower on typical price; exit RSI > 70; ROI 10%; SL 25%.
  hlhb                       freqtrade-strategies. 4h, long: RSI10 (of (open+close)/2) crosses
                             above 50 and EMA5 crosses above EMA10 and ADX > 25; exit the mirror
                             crosses with ADX > 25; ROI table 62.25% / 21.87% after 703 min /
                             3.63% after 2849 / 0 after 5520; SL 32.11%; trailing 1.17% once
                             +1.86%.
  Strategy001                freqtrade-strategies. Published on 5m, run on 15m (the finest Exness
                             candles here): EMA20 crosses above EMA50, Heikin-Ashi close > EMA20,
                             green HA bar; exit EMA50 crosses above EMA100, HA close < EMA20, red
                             HA bar; ROI 5% / 4% after 20 min / 3% after 30 / 1% after 60; SL 10%.
  Connors RSI-2              Larry Connors' RSI(2) (Short Term Trading Strategies That Work), on
                             1h, both sides: long close > SMA200 and RSI2 < 10, exit close > SMA5;
                             short mirrored (RSI2 > 90, close < SMA200, exit close < SMA5). No stop.
  Turtle System 1            Donchian 20-bar breakout, stop 2 x ATR20 (N), exit on the 10-bar
                             opposite breakout; 1h, both sides.
  London breakout            github.com/MHZardary/london-strategy-backtest: the Asian range
                             (00:00-07:00 UTC); a 15m close outside it between 07:00 and 08:30
                             UTC enters; SL the range's other side; TP 2 ranges beyond (3H - 2L);
                             out at 23:15 UTC; one trade a day. Both sides.
  PDH/PDL sweep              github.com/ikeawesom/xauusd-backtest's idea (its "71% win rate"
                             counts a WIN whenever the next bar opens at or above the entry - no
                             money is measured), given a real stop and target: with a bullish
                             previous day, price trades below its low (PDL) then a 15m bar closes
                             back above it -> long, stop at the sweep's low, target 1R, else out
                             at the day's end; bearish mirrored at PDH.
  TradePicker                the tool's own live engine and live exit (cfd_target_study.py),
                             today's targets and the closer R-multiple ones.

UNITS: $ per 1 BTC (one Exness lot) / per gold lot (100 oz). Swap not included.
PERIODS: in-sample to 15 Aug 2025, held-out to 11 Sep 2026, live window to 30 Sep 2026.

    python3 exness_strategy_shootout.py
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

SYMBOLS = {"BTCUSD": ("BTC", 1.0, 20), "XAUUSD": ("GOLD", 100.0, 25)}


# ---------------------------------------------------------------- indicators (Pine / TA-Lib definitions)
def rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up, dn = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    out = 100 - 100 / (1 + up / dn)
    out[dn == 0] = 100.0
    out[(up == 0) & (dn != 0)] = 0.0
    return out


def bbands(s, n, k):
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)                  # Pine's stdev and TA-Lib's are population
    return mid - k * sd, mid, mid + k * sd


def mfi(df, n):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    ch = tp.diff()
    upper = (df["Volume"] * tp.where(ch > 0, 0.0)).rolling(n).sum()
    lower = (df["Volume"] * tp.where(ch < 0, 0.0)).rolling(n).sum()
    out = 100 - 100 / (1 + upper / lower)
    out[lower == 0] = 100.0
    return out


def atr(df, n):
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def adx(df, n=14):
    up, dn = df["High"].diff(), -df["Low"].diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(df, n)
    pdi, ndi = 100 * rma(pdm, n) / a, 100 * rma(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    return rma(dx, n)


def crossed_above(a, b):
    b_prev = b.shift() if hasattr(b, "shift") else b              # a line, or a fixed level (RSI 50)
    return (a > b) & (a.shift() <= b_prev)


def crossed_below(a, b):
    b_prev = b.shift() if hasattr(b, "shift") else b
    return (a < b) & (a.shift() >= b_prev)


def heikin_ashi(df):
    ha_c = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha_o = ha_c.copy()
    o, c = df["Open"].to_numpy(), ha_c.to_numpy()
    out = np.empty(len(df))
    out[0] = (o[0] + df["Close"].iloc[0]) / 2
    for i in range(1, len(df)):
        out[i] = (out[i - 1] + c[i - 1]) / 2
    ha_o[:] = out
    return ha_o, ha_c


def resample(df, rule):
    """15m Exness bars (UTC) -> a coarser bar, with the spread a fill at its close would pay."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
           "spread_close": "last", "spread_avg": "mean"}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["Open"])


# ---------------------------------------------------------------- the one simulator
def simulate(df, signals, mult, stop_fn=None, target_fn=None, exit_long=None, exit_short=None,
             trail=None, exit_at=None, max_bars=2000):
    """One position at a time. A signal on bar i fills at bar i+1's OPEN (TradingView's
    and freqtrade's default). Inside a bar: the stop first (pessimistic), then the
    target; a gap beyond either fills at the open. Then, at the bar's close: the
    trailing stop's update, the strategy's own exit signal, its time exit. Exness's
    spread is paid half in (that bar's spread) and half out.

    signals: [(i, side)] side +1 long / -1 short. stop_fn(entry, i, side) -> price or None;
    target_fn(entry, i, side, minutes_held) -> price or None; exit_long/exit_short: bool
    arrays evaluated at a bar's close; trail: (pct, offset_pct); exit_at(i, j) -> True to
    close at bar j's close."""
    o, h, l, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    spc, spa = df["spread_close"].to_numpy(), df["spread_avg"].to_numpy()
    idx = df.index
    bar_min = (idx[1] - idx[0]).total_seconds() / 60
    n, out, free_from = len(df), [], 0
    for i, side in signals:
        k = i + 1
        if k >= n or k < free_from:
            continue
        entry = o[k]
        stop = stop_fn(entry, i, side) if stop_fn else None
        best = entry
        px = via = None
        for j in range(k, min(n, k + max_bars)):
            held = (j - k + 1) * bar_min
            if stop is not None and ((l[j] <= stop) if side > 0 else (h[j] >= stop)):
                gap = (o[j] < stop) if side > 0 else (o[j] > stop)
                px, via = (o[j] if gap and j > k else stop), "stop"
                break
            tgt = target_fn(entry, i, side, held) if target_fn else None
            if tgt is not None and ((h[j] >= tgt) if side > 0 else (l[j] <= tgt)):
                gap = (o[j] > tgt) if side > 0 else (o[j] < tgt)
                px, via = (o[j] if gap and j > k else tgt), "target"
                break
            best = max(best, h[j]) if side > 0 else min(best, l[j])
            if trail is not None:
                pct, off = trail
                if (best / entry - 1 if side > 0 else 1 - best / entry) >= off:
                    t = best * (1 - pct) if side > 0 else best * (1 + pct)
                    stop = t if stop is None else (max(stop, t) if side > 0 else min(stop, t))
            ex = exit_long if side > 0 else exit_short
            if (ex is not None and ex[j]) or (exit_at is not None and exit_at(i, j)):
                px, via = c[j], "exit"
                break
        if px is None:
            j = min(n, k + max_bars) - 1
            px, via = c[j], "end"
        out_sp = spa[j] if via in ("stop", "target") else spc[j]
        net = (side * (px - entry) - spc[i] / 2 - out_sp / 2) * mult
        out.append({"when": idx[i] + (idx[1] - idx[0]), "net": net, "side": side, "via": via, "bars": j - k + 1})
        free_from = j + 1
    return out


def pct_stop(p):
    return lambda e, i, s: e * (1 - p) if s > 0 else e * (1 + p)


def roi_target(table):
    """freqtrade's minimal_roi: {minutes: profit}, the latest threshold reached applies."""
    steps = sorted((float(m), float(r)) for m, r in table.items())

    def f(e, i, s, held):
        r = None
        for m, v in steps:
            if held >= m:
                r = v
        return None if r is None else (e * (1 + r) if s > 0 else e * (1 - r))
    return f


# ---------------------------------------------------------------- the strategies
def strategies(d15):
    """{name: (frame, signals, kwargs)} - each on its published timeframe."""
    out = {}
    c = d15["Close"]

    # Flawless Victory (15m, long only)
    r14 = rsi(c, 14)
    lo1, _, up1 = bbands(c, 20, 1.0)
    lo2, _, up2 = bbands(c, 17, 1.0)
    m14 = mfi(d15, 14)
    sig = [(i, 1) for i in np.flatnonzero(((c < lo1) & (r14 > 42)).to_numpy())]
    out["Flawless Victory v1"] = (d15, sig, dict(exit_long=((c > up1) & (r14 > 70)).to_numpy()))
    sig = [(i, 1) for i in np.flatnonzero(((c < lo2) & (r14 > 42)).to_numpy())]
    out["Flawless Victory v2"] = (d15, sig, dict(exit_long=((c > up2) & (r14 > 76)).to_numpy(),
                                                 stop_fn=pct_stop(0.06604),
                                                 target_fn=lambda e, i, s, h: e * 1.02328))
    sig = [(i, 1) for i in np.flatnonzero(((c < lo1) & (m14 < 60)).to_numpy())]
    out["Flawless Victory v3"] = (d15, sig, dict(exit_long=((c > up1) & (r14 > 65) & (m14 > 64)).to_numpy(),
                                                 stop_fn=pct_stop(0.08882),
                                                 target_fn=lambda e, i, s, h: e * 1.02317))

    # BbandRsi (1h, long only)
    d1h = resample(d15, "1h")
    tp = (d1h["High"] + d1h["Low"] + d1h["Close"]) / 3
    lo, _, _ = bbands(tp, 20, 2.0)
    r = rsi(d1h["Close"], 14)
    sig = [(i, 1) for i in np.flatnonzero(((r < 30) & (d1h["Close"] < lo)).to_numpy())]
    out["BbandRsi (1h)"] = (d1h, sig, dict(exit_long=(r > 70).to_numpy(), stop_fn=pct_stop(0.25),
                                           target_fn=roi_target({"0": 0.1})))

    # hlhb (4h, long only as published)
    d4h = resample(d15, "4h")
    hl2 = (d4h["Close"] + d4h["Open"]) / 2
    r10 = rsi(hl2, 10)
    e5, e10 = d4h["Close"].ewm(span=5, adjust=False).mean(), d4h["Close"].ewm(span=10, adjust=False).mean()
    ax = adx(d4h)
    buy = crossed_above(r10, 50) & crossed_above(e5, e10) & (ax > 25)
    sell = crossed_below(r10, 50) & crossed_below(e5, e10) & (ax > 25)
    out["hlhb (4h)"] = (d4h, [(i, 1) for i in np.flatnonzero(buy.to_numpy())],
                        dict(exit_long=sell.to_numpy(), stop_fn=pct_stop(0.3211),
                             target_fn=roi_target({"0": 0.6225, "703": 0.2187, "2849": 0.0363, "5520": 0}),
                             trail=(0.0117, 0.0186)))

    # Strategy001 (published 5m; 15m here)
    e20, e50, e100 = (c.ewm(span=s, adjust=False).mean() for s in (20, 50, 100))
    ha_o, ha_c = heikin_ashi(d15)
    buy = crossed_above(e20, e50) & (ha_c > e20) & (ha_o < ha_c)
    sell = crossed_above(e50, e100) & (ha_c < e20) & (ha_o > ha_c)
    out["Strategy001 (15m)"] = (d15, [(i, 1) for i in np.flatnonzero(buy.to_numpy())],
                                dict(exit_long=sell.to_numpy(), stop_fn=pct_stop(0.10),
                                     target_fn=roi_target({"60": 0.01, "30": 0.03, "20": 0.04, "0": 0.05})))

    # Connors RSI-2 (1h, both sides)
    cc = d1h["Close"]
    r2, s200, s5 = rsi(cc, 2), cc.rolling(200).mean(), cc.rolling(5).mean()
    sig = sorted([(i, 1) for i in np.flatnonzero(((cc > s200) & (r2 < 10)).to_numpy())]
                 + [(i, -1) for i in np.flatnonzero(((cc < s200) & (r2 > 90)).to_numpy())])
    out["Connors RSI-2 (1h)"] = (d1h, sig, dict(exit_long=(cc > s5).to_numpy(), exit_short=(cc < s5).to_numpy()))

    # Turtle System 1 (1h, both sides)
    hh20, ll20 = d1h["High"].rolling(20).max().shift(), d1h["Low"].rolling(20).min().shift()
    hh10, ll10 = d1h["High"].rolling(10).max().shift(), d1h["Low"].rolling(10).min().shift()
    nn = atr(d1h, 20).to_numpy()
    sig = sorted([(i, 1) for i in np.flatnonzero((cc > hh20).to_numpy())]
                 + [(i, -1) for i in np.flatnonzero((cc < ll20).to_numpy())])
    out["Turtle System 1 (1h)"] = (d1h, sig, dict(
        exit_long=(cc < ll10).to_numpy(), exit_short=(cc > hh10).to_numpy(),
        stop_fn=lambda e, i, s: e - 2 * nn[i] if s > 0 else e + 2 * nn[i]))

    # London breakout of the Asian range (15m, both sides)
    t = d15.index
    day = t.normalize()
    mins = (t.hour * 60 + t.minute).to_numpy()
    asian = (mins < 7 * 60)
    ah = d15["High"].where(asian).groupby(day).transform("max").to_numpy()
    al = d15["Low"].where(asian).groupby(day).transform("min").to_numpy()
    cl = c.to_numpy()
    win = (mins >= 7 * 60) & (mins + 15 <= 8 * 60 + 30)            # bars CLOSING 07:15 - 08:30
    sig, taken = [], set()
    for i in np.flatnonzero(win):
        dd = day[i]
        if dd in taken or not (ah[i] == ah[i]) or ah[i] <= al[i]:
            continue
        if cl[i] > ah[i]:
            sig.append((i, 1)); taken.add(dd)
        elif cl[i] < al[i]:
            sig.append((i, -1)); taken.add(dd)
    lb_stop = lambda e, i, s: al[i] if s > 0 else ah[i]
    lb_tgt = lambda e, i, s, h_: 3 * ah[i] - 2 * al[i] if s > 0 else 3 * al[i] - 2 * ah[i]
    out["London breakout (15m)"] = (d15, sig, dict(stop_fn=lb_stop, target_fn=lb_tgt,
                                                   exit_at=lambda i, j: day[j] != day[i] or mins[j] + 15 >= 23 * 60 + 15))

    # PDH / PDL sweep, with a real stop and target (15m)
    dayly = d15.groupby(day).agg(o=("Open", "first"), h=("High", "max"), l=("Low", "min"), c=("Close", "last"))
    prev = dayly.shift()
    pdh, pdl = prev["h"].reindex(day).to_numpy(), prev["l"].reindex(day).to_numpy()
    bull = (prev["c"] > prev["o"]).reindex(day).to_numpy()
    lo_, hi_ = d15["Low"].to_numpy(), d15["High"].to_numpy()
    sig, stops, taken = [], {}, set()
    swept_lo, swept_hi, cur = np.inf, -np.inf, None
    for i in range(len(d15)):
        if day[i] != cur:
            cur, swept_lo, swept_hi = day[i], np.inf, -np.inf
        if not (pdh[i] == pdh[i]) or cur in taken:
            continue
        if bull[i]:
            if lo_[i] < pdl[i] or swept_lo < np.inf:
                swept_lo = min(swept_lo, lo_[i])
                if swept_lo < pdl[i] and cl[i] > pdl[i]:
                    sig.append((i, 1)); stops[i] = swept_lo; taken.add(cur)
        else:
            if hi_[i] > pdh[i] or swept_hi > -np.inf:
                swept_hi = max(swept_hi, hi_[i])
                if swept_hi > pdh[i] and cl[i] < pdh[i]:
                    sig.append((i, -1)); stops[i] = swept_hi; taken.add(cur)
    out["PDH/PDL sweep (15m)"] = (d15, sig, dict(
        stop_fn=lambda e, i, s: stops[i],
        target_fn=lambda e, i, s, h_: e + (e - stops[i]) if s > 0 else e - (stops[i] - e),
        exit_at=lambda i, j: day[j] != day[i] or mins[j] + 15 >= 24 * 60))
    return out


def tradepicker(sym, key, mult, adx_gate, full):
    """The tool's own engine + live exit (cfd_target_study's variants), net after spread."""
    import backtest_intraday as bt
    import cfd_target_study as cts
    import config
    import crypto_strategy_study as cs
    import exness_adx_study as ex
    import exness_data as ed
    import indicators as ind
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    with open(os.path.join(ed._dir(), f"adx_{sym}_{adx_gate}_{len(df)}.pkl"), "rb") as fh:
        d = pickle.load(fh)
    adx_arr = bt.precompute(df, key)["adx"].to_numpy()
    st_line = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    sp_c, sp_a = full["spread_close"].to_numpy(), full["spread_avg"].to_numpy()
    res = {}
    for name, how in (("TradePicker today", None), ("TradePicker R 0.3/0.6", ("r", 0.3, 0.6)),
                      ("TradePicker R 0.5/1", ("r", 0.5, 1.0)), ("TradePicker R 1.5/3", ("r", 1.5, 3.0))):
        entries = [dict(tr, **dict(zip(("t1", "t2"), cts.levels(tr, how)))) for tr in d["entries"]]
        rows = cs.run_live(df, None, entries, d["opp"], st_line, adx_arr, be_bars=0, exit_key="t2")
        for r_ in rows:
            out_sp = sp_c[r_["j"]] if r_["closed_via"] == "other" else sp_a[r_["j"]]
            r_["net"] = ex.price_cfd(r_["side"], r_["entry"], r_["exit"], sp_c[r_["i"]], out_sp) * mult
        res[name] = rows
    return res


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import crypto_strategy_study as cs
    import exness_data as ed
    for sym, (key, mult, adx_gate) in SYMBOLS.items():
        full = ed.load(sym)
        d15 = full[["Open", "High", "Low", "Close", "Volume", "spread_close", "spread_avg"]].copy()
        d15.index = d15.index.tz_convert("UTC")
        results = tradepicker(sym, key, mult, adx_gate, full)
        for name, (frame, sig, kw) in strategies(d15).items():
            rows = simulate(frame, sig, mult, **kw)
            for r_ in rows:
                r_["when"] = r_["when"].tz_convert(full.index.tz)
            results[name] = rows
        print("\n" + "=" * 140)
        print(f" EXNESS {sym} - $ per {'1 BTC' if key == 'BTC' else 'gold lot (100 oz)'}, net after Exness's spread")
        print("=" * 140)
        print(f" {'strategy':24s} {'IN-SAMPLE  n   win%   net        PF    maxDD':>46s}  {'HELD-OUT  n   win%   net        PF    maxDD':>46s}  "
              f"{'LIVE  n  net':>16s}")
        for name, rows in sorted(results.items(), key=lambda kv: -cs.stats(cs.periods(kv[1])["oos"], "net")["total"]):
            p = cs.periods(rows)

            def c_(rs, wide=True):
                s = cs.stats(rs, "net")
                if not wide:
                    return f"{s['n']:>4} {s['total']:>+9,.0f}"
                return f"{s['n']:>5} {s['win']:>5.1f}% {s['total']:>+11,.0f} {s['pf']:>5.2f} {s['dd']:>9,.0f}"
            both = (cs.stats(p["is"], "net")["total"] > 0 and cs.stats(p["oos"], "net")["total"] > 0)
            print(f" {name:24s} {c_(p['is']):>46s}  {c_(p['oos']):>46s}  {c_(p['live'], False):>16s}"
                  f"  {'profit both' if both else ''}")


if __name__ == "__main__":
    main()
