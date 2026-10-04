#!/usr/bin/env python3
"""
btc_trend_study.py — classic daily TREND-FOLLOWING on Bitcoin, held to the same strict test
================================================================================
The user, 4 Oct 2026, after asking whether anyone has proven an 80%-win, small-loss crypto strategy (no
one has, honestly): "yes run the trend following test". Trend following wins LESS often (35-45% is
normal) and makes its money on the few long moves - the opposite shape to the 87% rule.

THE RULES - textbook versions, the usual published settings, NOT tuned here (18 variants, all fixed
before any result was seen):
  price vs SMA N            N = 50, 100, 200 days           (above = long, below = short / flat)
  SMA crossover f / s       20 / 100, 50 / 200 ("golden cross")
  Donchian (Turtle)  in / out   20 / 10, 55 / 20  (close beyond the N-day high/low; out on the M-day opposite)
  time-series momentum L    30, 90 days, reviewed each Monday   (the sign of the L-day return)
  each LONG/SHORT and LONG-ONLY (flat instead of short)

DATA: BTC-USD daily since 17 Sep 2014 (Yahoo Finance's public chart data; matches Exness's own daily
closes to 0.14% on 95% of the 1,123 days they share). Exness's 3 years alone would give too few trades.
TRADES: decided on a day's close, done at the next day's open (BTC trades 24/7, so that is the same
price a moment later). COSTS as on the demo account: the spread 0.04% of the price per round trip
(~$34 at $84k - above Exness's $10 today, for the older years' wider spreads), and Exness's swap on
BUYS, 0.0224% of the price a night (its -$19.02 per BTC at $84,772), Mon-Fri, x3 on Friday - about
8% a year on a long. Shorts pay none on this account.

THE TEST, fixed before the results:
  in-sample  17 Sep 2014 - 31 Dec 2020;   held-out  1 Jan 2021 - today
  KEEP only if profitable after costs in BOTH periods AND the held-out result beats 1 - 0.05/18 of
  RANDOM-TIMING runs (the rule's own trades, same directions and lengths, started on random days in
  the period: does it pick its moments, or just ride BTC being up?).
  Beside each: buy-and-hold as a CFD (the same swap), for scale.

    python3 btc_trend_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

SPREAD = 0.0004                  # round trip, of the price
SWAP_NIGHT = 19.023 / 84772.09   # of the price, per weekday night on a long (x3 Friday)
SPLIT = pd.Timestamp("2021-01-01", tz="UTC")
N_RUNS = 2000
K = 18


def load():
    p = os.path.expanduser("~/trading-tool-logs/history/daily/BTCUSD_1d_yahoo.csv")
    d = pd.read_csv(p, index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True)
    return d[["Open", "High", "Low", "Close"]].astype(float)


def swap_units(dates):
    """Exness rollovers on each calendar day: Mon-Thu 1, Fri 3, weekends 0."""
    wd = dates.weekday
    return np.where(wd == 4, 3, np.where(wd < 4, 1, 0)).astype(float)


def states(d, kind, a, b, long_only):
    """+1 / -1 / 0 decided at each day's close."""
    c, h, l = d["Close"], d["High"], d["Low"]
    if kind == "sma":
        s = np.where(c > c.rolling(a).mean(), 1, -1)
        s[:a] = 0
    elif kind == "cross":
        s = np.where(c.rolling(a).mean() > c.rolling(b).mean(), 1, -1)
        s[:b] = 0
    elif kind == "donchian":
        hi_in, lo_in = h.rolling(a).max().shift(), l.rolling(a).min().shift()
        hi_out, lo_out = h.rolling(b).max().shift(), l.rolling(b).min().shift()
        s, cur = np.zeros(len(d), int), 0
        for i in range(len(d)):
            ci = c.iloc[i]
            if cur == 1 and ci < lo_out.iloc[i]:
                cur = 0
            elif cur == -1 and ci > hi_out.iloc[i]:
                cur = 0
            if cur <= 0 and ci > hi_in.iloc[i]:
                cur = 1
            elif cur >= 0 and ci < lo_in.iloc[i]:
                cur = -1
            s[i] = cur
    elif kind == "tsmom":
        r = c / c.shift(a) - 1
        s, cur = np.zeros(len(d), int), 0
        for i in range(len(d)):
            if d.index[i].weekday() == 0 and not np.isnan(r.iloc[i]):
                cur = 1 if r.iloc[i] > 0 else -1
            s[i] = cur
    s = np.asarray(s, dtype=int)
    if long_only:
        s = np.where(s > 0, 1, 0)
    return s


def run(d, pos):
    """pos[t] decided at close t, held from open t+1 to open t+2. -> daily returns, trades."""
    o = d["Open"].to_numpy()
    held = np.concatenate([[0], pos[:-1]])                      # the position during day t (open t -> open t+1)
    ret = np.zeros(len(d))
    ret[:-1] = held[:-1] * (o[1:] / o[:-1] - 1)
    change = np.abs(np.diff(np.concatenate([[0], held])))       # entries/exits at day t's open
    ret -= change * SPREAD / 2
    ret -= (held > 0) * swap_units(d.index) * SWAP_NIGHT
    trades, i, n = [], 0, len(held)
    while i < n:
        if held[i] != 0:
            j = i
            while j + 1 < n and held[j + 1] == held[i]:
                j += 1
            r = np.prod(1 + ret[i:j + 1]) - 1
            trades.append((d.index[i], int(held[i]), j - i + 1, r))
            i = j + 1
        else:
            i += 1
    return ret, trades


def stats(ret, trades):
    eq = np.cumprod(1 + ret)
    years = len(ret) / 365.25
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq))) if len(eq) else 0.0
    rs = np.array([t[3] for t in trades]) if trades else np.array([])
    w, lo = rs[rs > 0], rs[rs <= 0]
    return {"trades": len(rs), "win": 100 * len(w) / len(rs) if len(rs) else 0.0,
            "avg_win": 100 * w.mean() if len(w) else 0.0, "avg_loss": 100 * lo.mean() if len(lo) else 0.0,
            "total": 100 * (eq[-1] - 1) if len(eq) else 0.0, "cagr": 100 * (eq[-1] ** (1 / years) - 1) if len(eq) else 0.0,
            "dd": 100 * dd, "exposure": 100 * float(np.mean(ret != 0))}


def random_timing(d_per, trades, rng, runs):
    """The rule's own trades (direction, length) started on random days of the period: total return of each run."""
    o = d_per["Open"].to_numpy()
    sw = swap_units(d_per.index) * SWAP_NIGHT
    csw = np.concatenate([[0.0], np.cumsum(sw)])
    n = len(o)
    out = np.empty(runs)
    for k in range(runs):
        tot = 1.0
        for _, side, length, _ in trades:
            if length >= n - 1:
                continue
            s = rng.integers(0, n - length - 1)
            r = side * (o[s + length] / o[s] - 1) - SPREAD - (csw[s + length] - csw[s] if side > 0 else 0.0)
            tot *= 1 + r
        out[k] = 100 * (tot - 1)
    return out


def main():
    d = load()
    rng = np.random.default_rng(20261004)
    rules = []
    for lo_ in (False, True):
        mode = "long-only" if lo_ else "long/short"
        for n_ in (50, 100, 200):
            rules.append((f"price vs SMA {n_} ({mode})", "sma", n_, None, lo_))
        for f, s_ in ((20, 100), (50, 200)):
            rules.append((f"SMA {f}/{s_} cross ({mode})", "cross", f, s_, lo_))
        for a, b in ((20, 10), (55, 20)):
            rules.append((f"Donchian {a}/{b} ({mode})", "donchian", a, b, lo_))
        for L in (30, 90):
            rules.append((f"momentum {L}d weekly ({mode})", "tsmom", L, None, lo_))
    assert len(rules) == K
    ins = d.index < SPLIT
    print(f"BTC daily {d.index[0].date()} -> {d.index[-1].date()}; in-sample to {SPLIT.date()}, held-out after. "
          f"Spread {SPREAD * 100:.2f}% a round trip, swap on longs {SWAP_NIGHT * 100:.4f}% a night (~8%/yr).\n")
    bh = np.ones(len(d), int)
    head = f"  {'rule':34s} {'IN-SAMPLE  trades win%  avgW%  avgL%  total%  CAGR%  maxDD%':>58s}   {'HELD-OUT  trades win%  avgW%  avgL%  total%  CAGR%  maxDD%':>58s}   {'p (timing)':>10s}"
    print(head)

    def row(name, pos, test=True):
        cells, held_trades = [], None
        for per in (ins, ~ins):
            dd_ = d[per]
            ret, tr = run(dd_, pos[per])
            s = stats(ret, tr)
            cells.append(f"{s['trades']:>6} {s['win']:>4.0f}% {s['avg_win']:>+6.1f} {s['avg_loss']:>+6.1f} {s['total']:>+8.0f} "
                         f"{s['cagr']:>+6.1f} {s['dd']:>6.0f}")
            if per is not ins:
                held_trades, s_ho = tr, s
            else:
                s_is = s
        p = None
        if test:
            null = random_timing(d[~ins], held_trades, rng, N_RUNS)
            p = (1 + np.sum(null >= s_ho["total"])) / (1 + N_RUNS)
        keep = test and s_is["total"] > 0 and s_ho["total"] > 0 and p <= 0.05 / K
        print(f"  {name:34s} {cells[0]:>58s}   {cells[1]:>58s}   {'' if p is None else f'{p:>10.4f}'}"
              f"{'  KEEP' if keep else ''}", flush=True)
        return {"name": name, "in": s_is, "out": s_ho, "p": p, "keep": keep}

    res = [row("buy & hold (CFD, swap paid)", bh, test=False)]
    for name, kind, a, b, lo_ in rules:
        res.append(row(name, states(d, kind, a, b, lo_)))
    kept = [r for r in res if r["keep"]]
    print(f"\n  {len(kept)} of {K} pass (profitable in both periods, held-out beats {100 * (1 - 0.05 / K):.2f}% of random-timing runs)"
          + (": " + "; ".join(r["name"] for r in kept) if kept else ""))
    # per calendar year, the whole history, for every rule that made money in both periods
    both = [r["name"] for r in res[1:] if r["in"]["total"] > 0 and r["out"]["total"] > 0]
    if both:
        print("\n  year by year (% return at 1x, after costs) - buy & hold and the rules profitable in both periods:")
        yrs = sorted(set(d.index.year))
        print("  " + f"{'':34s}" + "".join(f"{y:>8d}" for y in yrs))
        for name, kind, a, b, lo_ in [("buy & hold (CFD, swap paid)", None, None, None, None)] + [r for r in rules if r[0] in both]:
            pos = bh if kind is None else states(d, kind, a, b, lo_)
            ret, _ = run(d, pos)
            yr = pd.Series(ret, index=d.index).groupby(d.index.year).apply(lambda x: 100 * (np.prod(1 + x) - 1))
            print("  " + f"{name:34s}" + "".join(f"{yr.get(y, 0):>+8.0f}" for y in yrs))


if __name__ == "__main__":
    main()
