#!/usr/bin/env python3
"""
cfd_trend_filter_check.py — the 87% BTC rule's "price vs 200-candle average": other averages and trend measures
================================================================================
The user, 4 Oct 2026: "check price v 200 candles with other combinations". RSI-2 buys a sharp dip only ABOVE
the 200-candle average and sells a sharp spike only BELOW it. Everything else fixed (RSI-2 < 10 / > 90, ADX >= 25,
stop 3 x ATR, target 0.2R, the spread check), the trend side decided instead by - all named before the run:
  SMA 50 / 100 / 150 / 200 (live) / 300 / 400 | EMA 200 | the 1-hour trend (EMA80 vs EMA200 on 15m) |
  price vs yesterday's close | NO trend condition (every dip bought, every spike sold)
The live rule's exact base (real-tick outcomes, $10+ spread, swap, one at a time). A change counts only if more
profit than the live SMA 200 in BOTH periods AND its held-out beats 1 - 0.05/9 of 2,000 coin flips.
Also a robustness read: an edge that is real should not live on one exact number.

    python3 cfd_trend_filter_check.py
"""
import os
import sys

import numpy as np

FLOOR = 10.0


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_rules
    import cfd_tick_study as cts
    import cfd_vote_search as vs
    import config
    import exness_data as ed
    plan = config.CFD_RULES["BTC"]
    full = ed.load("BTCUSD")
    df = full[["Open", "High", "Low", "Close", "Volume"]]
    a = cfd_rules.compute(df)
    c = df["Close"]
    r2 = a["rsi2_value"]
    O = np.load(os.path.join(ed._dir(), "outcomes_close_BTCUSD.npz"))
    k = list(O["stops"]).index(plan["stop_atr"])
    j = int(np.argmin(np.abs(O["targets"] - plan["target_r"])))
    spc = full["spread_close"].to_numpy()
    eff = np.maximum(spc, FLOOR)
    spread_ok = np.round(eff / (plan["target_r"] * plan["stop_atr"] * a["atr"]), 9) < plan["max_spread_share"]
    net = O["net"][:, :, k:k + 1, j:j + 1].astype(float) - (eff - spc)[:, None, None, None]
    entry = full.index.tz_convert("UTC").asi8 + 15 * 60 * 10 ** 9
    long_sw, _, triple = cts.SWAP["BTCUSD"]
    rolls, cumw = vs.rollover_weights(entry[0], entry[-1], triple)
    sim = vs.Sim(net, O["ext"][:, :, k:k + 1, j:j + 1], O["ok"], entry, long_sw, rolls, cumw, 1.0)
    ins, oos = entry < vs.SPLIT, entry >= vs.SPLIT
    cv = c.to_numpy()
    trends = {f"SMA {n}": cv > c.rolling(n).mean().to_numpy() for n in (50, 100, 150, 200, 300, 400)}
    nan_mask = {f"SMA {n}": np.isnan(c.rolling(n).mean().to_numpy()) for n in (50, 100, 150, 200, 300, 400)}
    trends["EMA 200"] = cv > c.ewm(span=200, adjust=False).mean().to_numpy()
    trends["1-hour trend (EMA80 vs 200)"] = a["h1_trend"] > 0
    trends["price vs yesterday's close"] = a["d1_trend"] > 0
    order = ["SMA 50", "SMA 100", "SMA 150", "SMA 200", "SMA 300", "SMA 400", "EMA 200",
             "1-hour trend (EMA80 vs 200)", "price vs yesterday's close", "NO trend condition"]
    rng = np.random.default_rng(20261004)
    print(f"  {'trend side decided by':30s} {'IN-SAMPLE   n   win%      net    PF':>38s}   {'HELD-OUT   n   win%      net    PF':>38s}   {'0.25 lot 3 yrs':>14s}")
    res = {}
    for name in order:
        if name == "NO trend condition":
            side = np.where(r2 < 10, 1, np.where(r2 > 90, -1, 0))
        else:
            up = trends[name]
            down = ~up if name not in ("1-hour trend (EMA80 vs 200)", "price vs yesterday's close") else (
                a["h1_trend"] < 0 if name.startswith("1-hour") else a["d1_trend"] < 0)
            side = np.where((r2 < 10) & up, 1, np.where((r2 > 90) & down, -1, 0))
            if name in nan_mask:
                side = np.where(nan_mask[name], 0, side)
        side = np.where(a["adx25"] & spread_ok & O["ok"], side, 0).astype(np.int64)
        side[:450] = 0                                          # every average warmed up (SMA 400) for every variant
        cells, tot = [], 0.0
        for per, key in ((ins, "is"), (oos, "oos")):
            idx = np.flatnonzero(per & (side != 0))
            st = vs.stats(sim.run(idx, side[idx], 0, 0)[1])
            res[(name, key)] = (st, idx, side)
            tot += st["net"]
            cells.append(f"{st['n']:>5} {100 * st['win']:>5.1f}% {st['net']:>+9,.0f} {st['pf']:>5.2f}")
        print(f"  {name + (' (live)' if name == 'SMA 200' else ''):30s} {cells[0]:>38s}   {cells[1]:>38s}   {0.25 * tot:>+13,.0f}", flush=True)
    bi, bo = res[("SMA 200", "is")][0]["net"], res[("SMA 200", "oos")][0]["net"]
    better = [nm for nm in order if nm != "SMA 200" and res[(nm, "is")][0]["net"] > bi and res[(nm, "oos")][0]["net"] > bo]
    print("\n  more profit than the live SMA 200 in BOTH periods:", ", ".join(better) if better else "none")
    for nm in better:
        st, idx, side = res[(nm, "oos")]
        flips = np.array([sim.run(idx, rng.choice([-1, 1], len(idx)), 0, 0)[1].sum() for _ in range(2000)])
        p = (1 + np.sum(flips >= st["net"])) / 2001
        print(f"    {nm}: held-out coin flips as good {100 * p:.2f}% -> {'KEEP' if p <= 0.05 / 9 else 'fails the coin flip'}")


if __name__ == "__main__":
    main()
