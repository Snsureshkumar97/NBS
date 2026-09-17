#!/usr/bin/env python3
"""
trend_speed_study.py — would a faster trend measure than ADX(14) do better?
===========================================================================
PRE-DECLARED 17 Sep 2026, before this was run, at the user's request. Prompted
by that morning: Nifty rallied from the open while ADX(14) sat at 12.6-14.1,
because it averages its directional reading (DX) over 14 fifteen-minute candles
and the previous afternoon had been flat. DX itself was already 33.5.

What changes. The trend measure the engine reads - indicators.adx() - and so
both of the places it is used, exactly as switching it live would: the >= 20
gate a signal needs, and the trend-day range expansion. Nothing else.

  live   ADX(14): directional indicators and DX both smoothed over 14 candles
  A      ADX(7):  both smoothed over 7 candles             (a config change)
  B      fast:    indicators over 14, DX smoothed over 3   (an indicator change)

The gate stays at 20 in every variant. The rest is the live rule set: no
opening-range wait, T3 >= 1x the stop, no entry into an RSI divergence,
trend-day room, all three indices, exit at T2 - priced as pro_study prices it:
real expiries, per lot, Zerodha's charges, 0.25% slippage, drawdowns in time
order. A variant is adopted only if it makes MORE money than live in BOTH the
first two years and the held-out year. One setting each, not swept.

    python3 trend_speed_study.py [out.json]

RESULT, history to 11 Sep 2026, per lot after costs, all three indices (the
live row reproduces the live figures exactly):
                            first two years                  held-out year
                            n     profit    PF    DD         n     profit    PF    DD
  live ADX(14)            3,726  +573,879  1.14  195,985   2,570  +207,933  1.07  426,028
  A ADX(7)                5,311  +626,249  1.11  274,892   3,280  +247,530  1.07  359,794
  B fast (DI 14, DX 3)    3,627  +691,171  1.17  170,001   2,385  +250,536  1.09  300,843
Both pass the declared bar (more money than live in both periods).
  A: +52k | +40k, but 43% | 28% more trades, a lower profit factor in the first
     two years and a 79k deeper drawdown there.
  B: +117k | +43k on FEWER trades, a higher profit factor and a shallower worst
     drawdown in both periods (-26k | -125k). The better of the two on every line.
Where B's gain comes from: Bank Nifty's held-out loss halves (-103k -> -48k). On
Nifty + Sensex alone B makes slightly less (-15k | -12k) with a shallower
drawdown (113k vs 131k | 181k vs 192k) - so it is steadier there, not richer.
Two variants were tried, and the held-out year has been used to choose rules
before; B is not adopted by this file - that is the user's call.
Sanity check on 17 Sep 2026's rally: ADX(14) 12.6 -> 15.5 over the first three
candles; A cleared 20 on the 09:30 candle, B on the 09:45 candle.
"""
import json
import math
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import open_and_rr_study as base
import regime_study as rs

_REAL_ADX = ind.adx


def fast_adx(df, length=14, dx_len=3):
    """Variant B: +DI/-DI over `length`, DX smoothed over `dx_len` candles."""
    high, low = df["High"], df["Low"]
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = ind.true_range(df).ewm(alpha=1 / length, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / tr
    mdi = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / dx_len, adjust=False).mean().fillna(0)


VARIANTS = {
    "live ADX(14)": _REAL_ADX,
    "A ADX(7)": lambda df, length=14: _REAL_ADX(df, 7),
    "B fast (DI 14, DX over 3)": lambda df, length=14: fast_adx(df, 14, 3),
}


def main():
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in base.INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    out = {"results": {}}
    try:
        for name, fn in VARIANTS.items():
            ind.adx = fn
            print(f"--- {name}", flush=True)
            out["results"][name] = base.summary(base.run(hists, vix, nrv, wait=False, mult=1.0))
    finally:
        ind.adx = _REAL_ADX
    live = out["results"]["live ADX(14)"]
    out["verdict"] = {}
    for name in VARIANTS:
        if name.startswith("live"):
            continue
        r = out["results"][name]
        di = r["in_sample"]["all"]["total"] - live["in_sample"]["all"]["total"]
        do = r["held_out"]["all"]["total"] - live["held_out"]["all"]["total"]
        out["verdict"][name] = {"first_two_years_vs_live": di, "held_out_vs_live": do, "keep": di > 0 and do > 0}
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
