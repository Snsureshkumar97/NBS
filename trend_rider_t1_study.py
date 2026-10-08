#!/usr/bin/env python3
"""
trend_rider_t1_study.py — the Trend Rider with the tool's T1 trailing stop, against the Trend Rider as it runs (3 years)
================================================================================
The user, 8 Oct 2026, on learning Nifty and Bank Nifty (on the Trend Rider since 7 Oct) have no T1 trailing stop: "yes run
the backtest with t1 trailing".

The Trend Rider live (config.TREND_RIDER): a 15-minute close above EMA 50 and VWAP with ADX(14) > 20 and rising and +DI
over -DI (mirrored for a put), entered on the first such close; stop at the last 5 candles' low (high); one target at
2.75 x the risk; out by 15:15; one position at a time. Nothing moves between entry and exit (tickets.py: a plain-exit
ticket skips the T1 step-up, the Supertrend trail and the 2-hour breakeven).

THE T1 TRAILING STOP, as the tool's own rules do it: when T1 is touched the stop moves up to T1 (never down again), and
with the trail on it then follows the index's 15-minute Supertrend (config.supertrend_params) once that is tighter. The
Trend Rider has no T1 of its own, so T1 is swept: 1.0, 1.1 (the tool's own share - T1 is 40% of the way to its target,
and 40% of 2.75R is 1.1R) and 1.5 x the risk; plus the common "breakeven at 1R" for comparison.

Walked twice: on the 15-minute candles (exactly as pasted_combos_study.py - its no-T1 result reproduced and checked
here), and on the 3 years of 5-MINUTE candles from the 15-minute entry, which see more of a candle's path - the fairer
test of a trailing stop. Inside one candle: the stop (as it stood) before the target, a T1 touch moving the stop only
from the next candle on, a gap filled at the open. Priced as every Trend Rider study (vwap_ema_atr_study.price_trade):
the at-the-money option on its real expiry, 0.25% slippage a side, every charge, per lot. In-sample to 15 Aug 2025, then
the held-out final year. KEEP only if more profit than the live Trend Rider in BOTH periods.

    python3 trend_rider_t1_study.py
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import entry_timing_study as ets
import indicators as ind
import pasted_combos_study as pc
import stop_day_study as sds
import vwap_ema_atr_study as vea

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
LIVE_TR = tuple(k for k in INDICES if config.SYSTEM_DEFAULTS.get(k) == "trend_rider")
RR = config.TREND_RIDER["target_r"]
SWING = config.TREND_RIDER["swing"]
ADX_MIN = config.TREND_RIDER["adx_min"]
# (label, T1 as a multiple of the risk, what T1 does: "t1" stop to T1 / "be" stop to entry, Supertrend trail after it)
VARIANTS = [("live: no T1 trail", None, None, False),
            ("T1 at 1.0R, stop to T1", 1.0, "t1", False),
            ("T1 at 1.0R, stop to T1 + Supertrend", 1.0, "t1", True),
            ("T1 at 1.1R, stop to T1 (the tool's 40%)", 1.1, "t1", False),
            ("T1 at 1.1R, stop to T1 + Supertrend", 1.1, "t1", True),
            ("T1 at 1.5R, stop to T1", 1.5, "t1", False),
            ("T1 at 1.5R, stop to T1 + Supertrend", 1.5, "t1", True),
            ("at 1.0R, stop to breakeven", 1.0, "be", False)]
OUT_BY = pc.OUT_BY
BAR15, BAR5 = pd.Timedelta(minutes=15), pd.Timedelta(minutes=5)


def entries(df):
    """[(bar, side, entry, stop)] for every fresh Trend Rider signal, as pasted_combos_study.run() takes them."""
    long_, short = pc.signals(df, 2, ADX_MIN)
    l, h, c = df["Low"].to_numpy(), df["High"].to_numpy(), df["Close"].to_numpy()
    tod = np.array([t.time() for t in df.index])
    date = np.array([t.date() for t in df.index])
    last = (dt.datetime.combine(dt.date(2000, 1, 1), OUT_BY) - dt.timedelta(minutes=15)).time()
    out = []
    for i in range(60, len(df) - 1):
        side = "CE" if long_[i] and not long_[i - 1] else "PE" if short[i] and not short[i - 1] else None
        if side is None or tod[i] >= last or date[i + 1] != date[i]:
            continue
        entry = c[i]
        lo5, hi5 = l[max(0, i - SWING + 1):i + 1].min(), h[max(0, i - SWING + 1):i + 1].max()
        stop = lo5 if side == "CE" else hi5
        risk = entry - stop if side == "CE" else stop - entry
        if risk > 0:
            out.append((i, side, entry, stop))
    return out


def walk(bars, start, side, entry, stop, risk, t1_r, mode, trail, st_at, cutoff):
    """Over candles bars[start:] (o, h, l, c, day-flag arrays): (exit price, exit candle, via). The stop as it stood is
    checked before the target; a T1 touch moves the stop (to T1, or to entry) from the next candle on, and with the
    trail on it then follows the Supertrend once tighter."""
    o, h, l, c, day = bars
    ce = side == "CE"
    tgt = entry + RR * risk if ce else entry - RR * risk
    t1 = None if t1_r is None else (entry + t1_r * risk if ce else entry - t1_r * risk)
    t1_done = False
    d0 = day[start]
    j = start
    for j in range(start, len(c)):
        if day[j] != d0:
            return c[j - 1], j - 1, "square-off"
        if (l[j] <= stop) if ce else (h[j] >= stop):
            return (min(o[j], stop) if ce else max(o[j], stop)), j, "stop"
        if (h[j] >= tgt) if ce else (l[j] <= tgt):
            return (max(o[j], tgt) if ce else min(o[j], tgt)), j, "target"
        if t1 is not None and not t1_done and ((h[j] >= t1) if ce else (l[j] <= t1)):
            t1_done = True
            new = t1 if mode == "t1" else entry
            stop = max(stop, new) if ce else min(stop, new)
        if t1_done and trail:
            s = st_at(j)
            if s == s:
                stop = max(stop, s) if ce else min(stop, s)
        if cutoff[j]:
            return c[j], j, "square-off"
    return c[j], j, "square-off"


def _job(k):
    c = sds._context()
    df = c["hists"][k][["Open", "High", "Low", "Close", "Volume"]]
    days = set(df.index.date)
    sig = c["F"][k]["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    st15 = ind.supertrend(df, *config.supertrend_params(k))[0].to_numpy()
    last15 = (dt.datetime.combine(dt.date(2000, 1, 1), OUT_BY) - dt.timedelta(minutes=15)).time()
    tod15 = np.array([t.time() for t in df.index])
    b15 = (df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy(),
           np.array([t.date() for t in df.index]))
    d5 = ets.load_5m(k)
    t5 = d5.index
    b5 = (d5["Open"].to_numpy(), d5["High"].to_numpy(), d5["Low"].to_numpy(), d5["Close"].to_numpy(),
          np.array([t.date() for t in t5]))
    cut5 = np.array([t.time() >= (dt.datetime.combine(dt.date(2000, 1, 1), OUT_BY) - dt.timedelta(minutes=5)).time()
                     for t in t5])
    # the Supertrend a 5-minute candle can see: the last 15-minute candle closed before it began
    pos15 = df.index.searchsorted(t5 - BAR15, side="right") - 1
    ent = entries(df)
    out = {}
    for label, t1_r, mode, trail in VARIANTS:
        for res in ("15", "5"):
            rows, free = [], -1
            for i, side, entry, stop in ent:
                if i <= free:
                    continue
                risk = entry - stop if side == "CE" else stop - entry
                t_in = df.index[i] + BAR15
                if res == "15":
                    px, j, via = walk(b15, i + 1, side, entry, stop, risk, t1_r, mode, trail,
                                      lambda j: st15[j], tod15 >= last15)
                    t_out, free_next = df.index[j] + BAR15, j
                else:
                    p0 = int(t5.searchsorted(t_in))
                    if p0 >= len(t5) or t5[p0].date() != t_in.date():
                        continue
                    px, p, via = walk(b5, p0, side, entry, stop, risk, t1_r, mode, trail,
                                      lambda p: st15[pos15[p]] if pos15[p] >= 0 else np.nan, cut5)
                    t_out = t5[p] + BAR5
                    free_next = int(df.index.searchsorted(t5[p], side="right")) - 1
                s = sigma_by_day.get(t_in.date())
                net = vea.price_trade(k, days, t_in, entry, t_out, px, s, side == "CE") if s and s == s else None
                if net is not None:
                    rows.append({"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via, "index": k})
                free = free_next
            out[(label, res)] = rows
    # the live Trend Rider must be pasted_combos_study.py's own, trade for trade
    ref = pc.run(k, df, sigma_by_day, days, 2, 15, ADX_MIN, RR, SWING)
    mine = out[(VARIANTS[0][0], "15")]
    assert [(r["when"], round(r["net"], 6)) for r in ref] == [(r["when"], round(r["net"], 6)) for r in mine], \
        f"{k}: the no-T1 15-minute walk must reproduce pasted_combos_study.run()"
    return k, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying the Trend Rider with and without a T1 trailing stop, 15- and 5-minute candles...", flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = dict(ex.map(_job, INDICES))
    W = 172
    pool = lambda label, res, keys: sorted([r for k in keys for r in got[k][(label, res)]], key=lambda r: r["when"])
    for res, rname in (("5", "5-MINUTE CANDLES (the fairer test of a trail)"), ("15", "15-MINUTE CANDLES (the Trend Rider study's own walk)")):
        for keys, title in ((LIVE_TR, " + ".join(LIVE_TR) + " (on the Trend Rider live)"),) + tuple(((k,), k) for k in INDICES):
            print("=" * W)
            print(f" {title} - {rname}; per lot, after costs.  columns: trades, won, total, profit factor, worst drop, worst day")
            print("=" * W)
            print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
            base = pool(VARIANTS[0][0], res, keys)
            for label, *_ in VARIANTS:
                print(sds.line(f"   {label}", pool(label, res, keys)))
            print("   " + "-" * (W - 3))
            for label, *_ in VARIANTS[1:]:
                print(sds.verdict(label, pool(label, res, keys), base))
            print()


if __name__ == "__main__":
    main()
