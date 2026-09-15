#!/usr/bin/env python3
"""
lookahead_test.py — does the backtest ever use something that was not known yet?
===============================================================================
Every input the backtest decides a trade with is recomputed from the candles up
to and including the entry candle - nothing after it - and must equal what the
full-history run used at that candle. Any difference is look-ahead.

Checked on Nifty and Bank Nifty (whose pricing volatility borrows Nifty's), at
random candles plus the 09:15, 09:30, 09:45 and last candles of sampled days:
  * every precomputed indicator and the day-range statistics
  * the regime features: stalled trend, daily trend, opening range, and the
    volatility the option is priced with
  * the recommendation itself: direction, stop, targets, reward:risk, confidence
  * the RSI-divergence filter's answer
Reads the cached history; nothing live is touched.
"""
import collections
import math
import os
import sys
import tempfile

os.environ.setdefault("TRADING_TOOL_HOME", tempfile.mkdtemp())
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import regime_study as rs
import signal_engine as se

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


def same(a, b):
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return len(a or []) == len(b or []) and all(same(x, y) for x, y in zip(a or [], b or []))
    na = a is None or (isinstance(a, float) and math.isnan(a))
    nb = b is None or (isinstance(b, float) and math.isnan(b))
    if na or nb:
        return na and nb
    if isinstance(a, (bool, np.bool_)) or isinstance(b, (bool, np.bool_)) or isinstance(a, str):
        return a == b
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return a == b


def nrv_of(dfn):
    return np.log(rs.daily_close(dfn)).diff().rolling(20).std() * math.sqrt(252)


rng = np.random.default_rng(20260915)
vix = rs.load_vix()
nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
no_chain = se.compute_option_chain_signal(None)

for key in ("NIFTY", "BANKNIFTY"):
    print(f"== {key}")
    df = bt.fetch_history(key, years=3, use_cache=True)
    pre = bt.precompute(df)
    F = rs.features(df, vix, nrv_of(nifty))
    step = config.INSTRUMENTS[key]["strike_step"]
    closes = df["Close"].to_numpy()
    rsi_full = ind.rsi(df["Close"], 14).to_numpy()
    days = sorted(set(df.index.date))
    picks = set(rng.integers(400, len(df) - 1, 25).tolist())
    for d in rng.choice(days[30:-1], 5, replace=False):
        where = np.flatnonzero(df.index.date == d)
        picks.update([int(where[0]), int(where[min(1, len(where) - 1)]), int(where[min(2, len(where) - 1)]), int(where[-1])])
    bad = collections.defaultdict(list)
    recs = 0
    for i in sorted(picks):
        t = df.index[i]
        dtr = df.iloc[:i + 1]
        ptr = bt.precompute(dtr)
        for col in pre.columns:
            if col == "is_day_end":
                continue        # see below: cut history always ends "at the end of a day"
            if not same(pre[col].iloc[i], ptr[col].iloc[-1]):
                bad[f"precompute.{col}"].append(str(t))
        # is_day_end marks the session's last candle, used only to square off at the
        # bell. Cut the history at any candle and that candle is the last one there,
        # so the truncated copy says True - an artifact of the cut, not a leak. What
        # matters is that it can be known from the clock: it must be True exactly on
        # the last candle stamped that date, which the exchange timetable fixes.
        same_day = np.flatnonzero(df.index.date == t.date())
        if bool(pre["is_day_end"].iloc[i]) != (i == int(same_day[-1])):
            bad["precompute.is_day_end (clock)"].append(str(t))
        Ftr = rs.features(dtr, vix, nrv_of(nifty[nifty.index <= t]))
        for col in ("stalled", "htf_up", "or_ready", "sigma"):
            if not same(F[col].iloc[i], Ftr[col].iloc[-1]):
                bad[f"features.{col}"].append(str(t))
        if bool(F["or_ready"].iloc[i]):
            for col in ("above_or", "below_or"):
                if not same(F[col].iloc[i], Ftr[col].iloc[-1]):
                    bad[f"features.{col}"].append(str(t))
        if np.isfinite(pre["typical"].iloc[i]) and i >= 300:
            def rec_of(d_, p_, j):
                tech = bt.tech_at(d_, p_, j)
                now = d_.index[j].to_pydatetime()
                reach = se.compute_reachability(tech["last_close"], no_chain, d_, now, adx=tech["adx"],
                                                range_stats=(float(p_["typical"].iloc[j]), float(p_["used_today"].iloc[j])),
                                                index_key=key,
                                                day_extremes=(float(p_["day_high"].iloc[j]), float(p_["day_low"].iloc[j])))
                return se.build_recommendation(key, tech, no_chain, step, reach=reach)
            a, b = rec_of(df, pre, i), rec_of(dtr, ptr, len(dtr) - 1)
            recs += 1
            for fld in ("bias", "option_type", "confidence", "index_stop_loss", "risk_points", "reach_to_risk", "index_targets"):
                if not same(a.get(fld), b.get(fld)):
                    bad[f"recommendation.{fld}"].append(str(t))
        rsi_tr = ind.rsi(dtr["Close"], 14).to_numpy()
        for side in ("CE", "PE"):
            if ind.rsi_divergence(closes[:i + 1], rsi_full[:i + 1], side) != ind.rsi_divergence(closes[:i + 1], rsi_tr, side):
                bad["divergence"].append(str(t))
    check(f"{key}: {len(picks)} candles, {recs} recommendations - every input known at the entry candle",
          not bad, {k: v[:3] for k, v in bad.items()})

print("LOOKAHEAD TEST PASSED" if not fails else f"LOOKAHEAD TEST FAILED: {fails}")
