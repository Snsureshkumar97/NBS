#!/usr/bin/env python3
"""
cfd_tick_flow.py — an ORDER-FLOW proxy for every 15-minute candle, from Exness's own ticks
================================================================================
The user, 4 Oct 2026: "can you check if order flow will improve the results". True order flow - who
bought aggressively and who sold - is not in Exness's data (a broker's quote feed has no trade sides),
and Binance's public taker-volume data refuses this location (HTTP 451, "unavailable for legal
reasons"; not routed around). So the standard stand-in when trade sides are unknown: the TICK RULE -
each change of the quoted mid is an up-tick (buying pressure) or a down-tick (selling pressure).
Per candle: the up-ticks, the down-ticks, and all ticks.

Output: <exness cache>/tickflow_<SYM>.npz - up, down, ticks per bar, aligned with exness_data.load(SYM).

    python3 cfd_tick_flow.py
"""
import os
import sys

import numpy as np

BAR_NS = 15 * 60 * 10 ** 9


def build(sym):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfd_tick_study as t
    import exness_data as ed
    full = ed.load(sym)
    starts = full.index.tz_convert("UTC").asi8
    up = np.zeros(len(full)); dn = np.zeros(len(full)); n = np.zeros(len(full))
    prev_last = None
    for (y, m) in ed.months_until(t.LAST):
        got = t._month_ticks(sym, y, m)
        if got is None:
            continue
        T, M, _ = got
        dm = np.diff(M, prepend=M[0] if prev_last is None else prev_last)
        prev_last = M[-1]
        b = np.searchsorted(starts, T, side="right") - 1          # the bar each tick falls in
        ok = (b >= 0) & (T < starts[np.clip(b, 0, None)] + BAR_NS)
        b, dm = b[ok], dm[ok]
        n += np.bincount(b, minlength=len(full))[:len(full)]
        up += np.bincount(b, weights=(dm > 0).astype(float), minlength=len(full))[:len(full)]
        dn += np.bincount(b, weights=(dm < 0).astype(float), minlength=len(full))[:len(full)]
        print(f"  {sym} {y}-{m:02d}: {len(T):,} ticks", flush=True)
    path = os.path.join(ed._dir(), f"tickflow_{sym}.npz")
    np.savez(path, up=up, down=dn, ticks=n, bar_start=starts)
    return path


if __name__ == "__main__":
    print(build("BTCUSD"))
