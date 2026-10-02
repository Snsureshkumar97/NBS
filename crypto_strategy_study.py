#!/usr/bin/env python3
"""
crypto_strategy_study.py — which BTC strategy actually makes money, priced as OPTIONS
================================================================================
The user, 2 Oct 2026: "i want you to work on btc strategy its giving loss can you
find the best strategy for crypto and backtest and give me results" - then pasted
two AI-generated crypto checklists while this was being built, both folded in below
as pre-declared candidates.

WHY A NEW HARNESS
    Every earlier BTC study here (time_breakeven_study.study_bitcoin,
    crypto_reward_risk_study.py, gann_volume_study) scored trades in raw index
    points with no costs, on the stated belief that "BTC is a Delta Exchange
    futures/perp position". It is not: delta_orders.py BUYS OPTIONS (C-BTC-<strike>-
    <DDMMYY>). For an option buyer, time decay, the implied volatility paid, the
    spread and Delta's fees decide the result - and points alone hide all four.
    This file prices every trade BOTH ways, so the vehicle question is answered too:
      * option  - Black-Scholes at Deribit's DVOL (crypto_data.dvol(), the market's
                  30-day implied vol, previous complete hour only), strike to the
                  nearest 200, Delta India's real option fee - 0.01% of notional,
                  capped at 3.5% of premium (read from Delta's public product list,
                  2 Oct 2026) - plus 18% GST, plus SPREAD_RT of premium round trip.
      * perp    - the same entry/exit on the perpetual, Delta's 0.05% taker each
                  side plus GST. No option involved, no decay.
    Money is per 1 BTC of underlying (1,000 Delta contracts). The user's usual 250
    lots is 0.25 BTC - the summary scales to that.

WHICH CONTRACT THE LIVE TOOL ACTUALLY BUYS (calibrated, not assumed)
    The live log has no expiry column, so it was inferred: each of the user's 124
    real BTC entries' premium, strike, spot and that hour's DVOL, solved for time to
    expiry. Only 3% sat on the nearest daily expiry; ~half were about a day further
    out, ~half ~37-44 days out (MAX_SPREAD_PCT picks the nearest expiry with a tight
    spread). So "next_day" (the daily after the nearest) and "monthly" (40 days) are
    modelled; "nearest" is kept only to test the pasted afternoon-theta warning.

THE CANDIDATES (all pre-declared before any result was read)
    A  Today's live rule engine on BTC - backtest_intraday.run() (the real
       build_recommendation(), crypto ADX/Wilder settings) + tickets.py's own
       _reward_hold (crypto 2.0) and _spread_hold (no chain in history -> passes);
       the opening-range and RSI-divergence gates switch themselves off for crypto.
       Exit exactly as live: stop; T1 touch -> stop to T1; Supertrend trail after
       T1; breakeven after 8 bars (TIME_BREAKEVEN_MINUTES 120) without T1; close at
       T2 (EXIT_AT_TARGET); EARLY_EXIT_ON_REVERSAL (a bar CLOSING on the clear
       opposite side - reversal_exit_study.py's own approximation, optimistic); 96
       bars max. One position at a time, 20-minute same-direction cooldown with the
       deployed trending-target waiver.
       A-variants: reward:risk gate 1.0 (crypto_reward_risk_study's best); without
       the reversal exit; without the Supertrend trail (both NEVER tested on BTC).
    B  The pasted "momentum breakout": a 15-minute close through the prior 20 bars'
       high (or low) - the CROSS, not every bar after it. Stop 1.5 x ATR(14), target
       2R (the paste's own 1:2 floor), 96 bars max. One position at a time.
       Filters from the two pastes, singly then together:
         vol      breakout candle volume >= 2x the previous 10 candles' average
         room60   today (UTC day, the 05:30 IST reset) has used < 60% of the
                  14-day DAILY ATR (previous days only)      [paste 1]
         room50   ... < 50%                                    [paste 2]
         funding  no long while 8h funding > +0.03%, no short while < -0.03%
                  ("deeply skewed" - 0.03% is 3x the standard 0.01% rate)
         us       entries only 18:30-23:30 IST (US session)    [paste 2]
         no_asia  no entries 09:00-14:00 IST                    [paste 2]
       NOT TESTABLE, stated not faked: open interest (no free history), "BTC
       alignment" (meaningless when trading BTC itself), leverage (an option buyer's
       loss is capped at the premium; there is no liquidation).
    C  A research-backed higher-timeframe trend follower (time-series momentum, the
       best-documented edge in crypto): 4-hour Donchian - enter on a close through
       the prior 20 four-hour bars, exit on a close through the prior 10 bars' other
       side, initial stop 2 x ATR(14, 4h). Multi-day holds, so priced on the monthly
       option and the perp only.

PERIODS: in-sample to 15 Aug 2025 (regime_study.SPLIT), held-out 15 Aug 2025 to
11 Sep 2026, and the LIVE WINDOW 11 Sep - 2 Oct 2026 - the exact days of the user's
real record, for a direct check of whether this harness reproduces it.

    python3 crypto_strategy_study.py
"""
import math
import os
import pickle

import numpy as np
import pandas as pd

import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import crypto_data as cd
import indicators as ind
import regime_study as rs
import reversal_exit_study as res
import signal_engine as se
import tickets

KEY = "BTC"
SPLIT = rs.SPLIT
LIVE_FROM = pd.Timestamp("2026-09-11 05:30", tz="Asia/Kolkata")
YEAR_H = 365 * 24
OPT_STEP = 200
FEE_OPT, FEE_CAP, FEE_PERP, GST = 0.0001, 0.035, 0.0005, 0.18
FEE_PERP_MAKER = 0.0002              # Delta's perpetual maker rate - only if a resting limit fills
SPREAD_RT = 0.02                     # option spread paid, round trip, as a share of premium
MIN_PREMIUM = 20.0                   # $ per BTC - below this a quote is noise
SETTLE_H, SETTLE_M = 17, 30          # Delta's BTC options settle 12:00 UTC = 17:30 IST
BAR = pd.Timedelta(minutes=15)
_SELF = object()


# ---------------------------------------------------------------- contracts and pricing
def expiry_for(t0, policy):
    """The settlement time of the contract bought at t0 (tz-aware IST)."""
    if policy == "monthly":
        return t0 + pd.Timedelta(days=40)
    nearest = t0.normalize() + pd.Timedelta(hours=SETTLE_H, minutes=SETTLE_M)
    if nearest <= t0:
        nearest += pd.Timedelta(days=1)
    if policy == "nearest":
        return nearest
    if policy == "next_day":
        return nearest + pd.Timedelta(days=1)
    raise ValueError(policy)


def option_fee(premium, spot):
    return min(FEE_OPT * spot, FEE_CAP * premium) * (1 + GST)


def price_option(side, S0, t0, S1, t1, iv0, iv1, policy, settle_spot=None):
    """(net $ per 1 BTC, entry premium) for buying the option at t0 and selling
    at t1. If the contract settles before t1 it is worth its intrinsic value at
    settlement (settle_spot), and no exit fee or spread is paid."""
    call = side == "CE"
    K = round(S0 / OPT_STEP) * OPT_STEP
    exp = expiry_for(t0, policy)
    T0 = (exp - t0).total_seconds() / 3600 / YEAR_H
    p0 = rs.bs(S0, K, T0, iv0, call)
    if p0 < MIN_PREMIUM:
        return None, p0
    if t1 >= exp:
        S = settle_spot if settle_spot is not None else S1
        p1 = max(0.0, (S - K) if call else (K - S))
        cost = option_fee(p0, S0) + SPREAD_RT / 2 * p0
    else:
        T1 = (exp - t1).total_seconds() / 3600 / YEAR_H
        p1 = rs.bs(S1, K, T1, iv1, call)
        cost = option_fee(p0, S0) + option_fee(p1, S1) + SPREAD_RT / 2 * (p0 + p1)
    return p1 - p0 - cost, p0


def price_perp(side, S0, S1, fee=FEE_PERP):
    sign = 1 if side == "CE" else -1
    return sign * (S1 - S0) - fee * (S0 + S1) * (1 + GST)


# ---------------------------------------------------------------- exits (index points)
def walk_live(hi, lo, cl, i, side, entry, stop, t1, target, st_line=None, be_bars=8,
              max_bars=96, opp=None):
    """Today's live BTC exit, bar by bar from i+1. Stop first inside a bar (the
    pessimistic order every study here uses), then T1 (moves the stop to T1 for
    the bars after), then the target closes it, then the stop updates that take
    effect from the next bar (breakeven after be_bars without T1, the Supertrend
    trail after T1), then a close on the clear opposite side. Returns
    (exit_price, exit_bar, closed_via)."""
    ce = side == "CE"
    n = len(cl)
    t1_done, be_done = False, False
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return stop, j, "stop"
        if not t1_done and t1 is not None and ((hi[j] >= t1) if ce else (lo[j] <= t1)):
            t1_done = True
            stop = max(stop, t1) if ce else min(stop, t1)
        if target is not None and ((hi[j] >= target) if ce else (lo[j] <= target)):
            return target, j, "target"
        set_by_price = stop                  # the stop as price itself has set it this bar (T1)
        if be_bars and not t1_done and not be_done and j - i >= be_bars:
            stop = max(stop, entry) if ce else min(stop, entry)
            be_done = True
        if st_line is not None and t1_done:
            s = st_line[j]
            if s == s:
                stop = max(stop, s) if ce else min(stop, s)
        # A stop moved at the bar's close (breakeven, the trail) can land BEYOND the
        # price - breakeven on a trade that is under water does exactly that. Live, the
        # next tick then closes it at the MARKET, not at the stop: filling at the stop
        # would be a price nobody could get. (T1's own move is different - price itself
        # touched T1, so a fall back through it fills near T1, handled above.)
        if (cl[j] <= stop) if ce else (cl[j] >= stop):
            if (cl[j] <= set_by_price) if ce else (cl[j] >= set_by_price):
                return set_by_price, j, "stop"   # touched T1 then fell back through it: fills near T1
            return cl[j], j, "stop"
        if opp is not None and opp[j] is not None and opp[j] != side:
            return cl[j], j, "reversal"
    last = min(i + max_bars, n - 1)
    return cl[last], last, "time"


def walk_simple(hi, lo, cl, i, side, stop, target, max_bars=96):
    """Stop / target / time - the breakout candidate's exit."""
    return walk_live(hi, lo, cl, i, side, None, stop, None, target, st_line=None, be_bars=0,
                     max_bars=max_bars, opp=None)


# ---------------------------------------------------------------- sequencing
def sequential(trades, cooldown_min=0, waiver_adx=None):
    """One open position at a time, in entry order. cooldown_min / waiver_adx
    reproduce the live same-direction cooldown and its trending-target waiver
    (conditional_cooldown_study.sequential_trades_conditional - reused, not
    rewritten); cooldown 0 is plain exclusivity."""
    trades = sorted(trades, key=lambda r: r["when"])
    if cooldown_min:
        return ccs.sequential_trades_conditional(trades, base_cooldown_min=cooldown_min,
                                                 waive_adx_threshold=waiver_adx if waiver_adx else 1e9)
    kept, open_until = [], None
    for tr in trades:
        if open_until is not None and tr["when"] < open_until:
            continue
        kept.append(tr)
        open_until = tr["exit_time"]
    return kept


# ---------------------------------------------------------------- stats
def stats(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0, "total": 0.0, "pf": 0.0, "dd": 0.0, "win": 0.0}
    eq = np.cumsum(vals)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0
    gain = sum(v for v in vals if v > 0)
    loss = -sum(v for v in vals if v < 0)
    return {"n": len(vals), "total": float(sum(vals)), "pf": gain / loss if loss else float("inf"),
            "dd": max(dd, 0.0), "win": 100.0 * sum(1 for v in vals if v > 0) / len(vals)}


def periods(rows):
    return {"is": [r for r in rows if r["when"] < SPLIT],
            "oos": [r for r in rows if SPLIT <= r["when"] < LIVE_FROM],
            "live": [r for r in rows if r["when"] >= LIVE_FROM]}


# ---------------------------------------------------------------- data, once
def load():
    df = cd.candles()
    v = cd.dvol()["dvol"].shift(1)                 # the previous COMPLETE hour only
    f = cd.funding()["funding_8h"].shift(1)
    v.index = v.index.tz_convert(df.index.tz)      # same instants, the candles' own tz object
    f.index = f.index.tz_convert(df.index.tz)
    close_t = df.index + BAR
    iv = pd.Series(v.reindex(close_t, method="ffill").to_numpy() / 100.0, index=df.index)
    fund = pd.Series(f.reindex(close_t, method="ffill").to_numpy(), index=df.index)
    return df, iv, fund


def utc_day_room(df):
    """Share of the 14-day DAILY ATR (UTC days - Delta's 05:30 IST reset,
    previous days only) today's high-low has used by each bar's close."""
    utc_day = df.index.tz_convert("UTC").date
    s = pd.Series(utc_day, index=df.index)
    d = df.groupby(utc_day).agg(High=("High", "max"), Low=("Low", "min"),
                                Open=("Open", "first"), Close=("Close", "last"))
    atr_prev = ind.atr(d, 14).shift(1)
    used = df.groupby(utc_day)["High"].cummax() - df.groupby(utc_day)["Low"].cummin()
    a = s.map(atr_prev).astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a > 0, used.to_numpy() / a, np.nan)


def cached(name, build):
    path = os.path.join(os.path.dirname(bt._cache_path(KEY, 3)), name)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
    out = build()
    with open(path, "wb") as fh:
        pickle.dump(out, fh)
    return out


# ---------------------------------------------------------------- candidate A: the live engine
def live_entries(df, need_rr, tag):
    """bt.run() with tickets.py's own crypto gates - the trades the live rule
    engine would have issued. Cached per data length and reward:risk setting."""
    def gate(i, rec):
        return (tickets.TicketBook._reward_hold(_SELF, KEY, rec) is None
                and tickets.TicketBook._spread_hold(_SELF, rec) is None)

    def build():
        was = dict(config.MIN_REWARD_RISK_T3)
        try:
            config.MIN_REWARD_RISK_T3["crypto"] = need_rr
            return bt.run(KEY, df, gate=gate)["trades"]
        finally:
            config.MIN_REWARD_RISK_T3.clear()
            config.MIN_REWARD_RISK_T3.update(was)
    return cached(f"btc_live_entries_{tag}_{len(df)}.pkl", build)


def run_live(df, iv, entries, opp, st_line, adx, trail=True, reversal=True, be_bars=8, exit_key="t2"):
    """The live engine walked in INDEX points - what a perpetual position on the
    same signals would do."""
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    pos = {t: n for n, t in enumerate(df.index)}
    out = []
    for tr in entries:
        i = pos[tr["when"]]
        px, j, via = walk_live(hi, lo, cl, i, tr["side"], tr["entry"], tr["stop"], tr.get("t1"),
                               tr.get(exit_key), st_line=st_line if trail else None, be_bars=be_bars,
                               max_bars=96, opp=opp if reversal else None)
        out.append({"when": df.index[i] + BAR, "exit_time": df.index[j] + BAR, "side": tr["side"],
                    "entry": tr["entry"], "exit": px, "i": i, "j": j,
                    "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                    "exit_adx": float(adx[j])})
    return sequential(out, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)


def premium_levels(tr, p0):
    """The ticket's frozen premium targets and stop, exactly as build_recommendation()
    sets them for a live chain quote: on a market-reach basis, the entry premium plus
    (or minus) the index distance x APPROX_ATM_DELTA; otherwise PREMIUM_TARGET_PCTS /
    PREMIUM_SL_PCT of the entry premium. Premium rising is good for calls AND puts."""
    S0, d = tr["entry"], config.APPROX_ATM_DELTA
    if tr.get("target_basis") == "market_reach":
        tg = [p0 + abs(t - S0) * d if t is not None else None for t in (tr.get("t1"), tr.get("t2"), tr.get("t3"))]
        sl = max(0.05, p0 - abs(tr["stop"] - S0) * d)
    else:
        tg = [p0 * (1 + pct / 100) for pct in config.PREMIUM_TARGET_PCTS]
        sl = p0 * (1 - config.PREMIUM_SL_PCT / 100)
    return tg, sl


def walk_live_premium(hi, lo, cl, ivs, close_t, i, tr, policy, st_line=None, be_bars=8,
                      exit_key="t2", max_bars=96, opp=None):
    """The live exit as tickets._check_price runs it for a premium-tracked ticket -
    which is every one of the user's 125 real BTC tickets (tracked_on = premium).
    The contract's premium is Black-Scholes at each bar (that bar's DVOL, the time
    left to its own expiry); a bar's best/worst premium is taken at the index's
    high/low (premium is monotone in the index). Stop first, then T1 (stop -> T1's
    premium), then the exit target, then breakeven / the Supertrend trail (converted
    to premium the way tickets.py converts it: entry premium + side x delta x the
    index gap), then a reversal close, then the contract expiring.
    Returns (entry premium, exit premium, exit bar, closed_via) or None."""
    side = tr["side"]
    call = side == "CE"
    S0 = tr["entry"]
    t0 = close_t[i]
    K = round(S0 / OPT_STEP) * OPT_STEP
    exp = expiry_for(t0, policy)
    yrs = lambda t: max((exp - t).total_seconds(), 0.0) / 3600 / YEAR_H
    p0 = rs.bs(S0, K, yrs(t0), ivs[i], call)
    if p0 < MIN_PREMIUM:
        return None
    tg, stop = premium_levels(tr, p0)
    t1p = tg[0]
    tgt = tg[["t1", "t2", "t3"].index(exit_key)]
    sign = 1 if call else -1
    t1_done, be_done = False, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + max_bars, n)):
        t, v = close_t[j], ivs[j]
        if t >= exp:
            settle = cl[j]
            return p0, max(0.0, (settle - K) if call else (K - settle)), j, "expiry"
        T = yrs(t)
        p_best = rs.bs(hi[j] if call else lo[j], K, T, v, call)
        p_worst = rs.bs(lo[j] if call else hi[j], K, T, v, call)
        if p_worst <= stop:
            return p0, stop, j, "stop"
        if not t1_done and t1p is not None and p_best >= t1p:
            t1_done = True
            stop = max(stop, t1p)
        if tgt is not None and p_best >= tgt:
            return p0, tgt, j, "target"
        set_by_price = stop
        if be_bars and not t1_done and not be_done and j - i >= be_bars:
            stop = max(stop, p0)
            be_done = True
        if st_line is not None and t1_done:
            s = st_line[j]
            if s == s:
                stop = max(stop, p0 + sign * config.APPROX_ATM_DELTA * (s - S0))
        p_close = rs.bs(cl[j], K, T, v, call)
        if p_close <= stop:                  # moved past the market: closed AT the market (see walk_live)
            return p0, (set_by_price if p_close <= set_by_price else p_close), j, "stop"
        if opp is not None and opp[j] is not None and opp[j] != side:
            return p0, p_close, j, "reversal"
    last = min(i + max_bars, n - 1)
    return p0, rs.bs(cl[last], K, yrs(close_t[last]), ivs[last], call), last, "time"


def run_live_premium(df, iv, entries, opp, st_line, adx, policy, trail=True, reversal=True,
                     be_bars=8, exit_key="t2"):
    """The live engine on OPTIONS, premium-tracked as live. P&L goes in
    r[f"opt_{policy}"], after Delta's fees both ends and the spread."""
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    ivs = iv.to_numpy()
    close_t = df.index + BAR
    pos = {t: n for n, t in enumerate(df.index)}
    out = []
    for tr in entries:
        i = pos[tr["when"]]
        if not (ivs[i] == ivs[i]):
            continue
        got = walk_live_premium(hi, lo, cl, ivs, close_t, i, tr, policy,
                                st_line=st_line if trail else None, be_bars=be_bars,
                                exit_key=exit_key, opp=opp if reversal else None)
        if got is None:
            continue
        p0, p1, j, via = got
        if via == "expiry":
            cost = option_fee(p0, tr["entry"]) + SPREAD_RT / 2 * p0
        else:
            cost = option_fee(p0, tr["entry"]) + option_fee(p1, cl[j]) + SPREAD_RT / 2 * (p0 + p1)
        out.append({"when": close_t[i], "exit_time": close_t[j], "side": tr["side"],
                    "entry": tr["entry"], "exit": cl[j], "i": i, "j": j,
                    f"opt_{policy}": p1 - p0 - cost, f"prem_{policy}": p0,
                    f"ret_{policy}": (p1 / p0 - 1) * 100,          # before costs, as the live log records it
                    "closed_via": "target" if via == "target" else ("stop" if via == "stop" else "other"),
                    "exit_adx": float(adx[j])})
    return sequential(out, cooldown_min=config.REENTRY_COOLDOWN_MIN, waiver_adx=config.ADX_TREND_THRESHOLD)


# ---------------------------------------------------------------- candidate B: momentum breakout
def breakout_entries(df, fund, room, lookback=20, atr_mult=1.5, rr=2.0, filters=()):
    hi, lo, cl, vol = (df[c].to_numpy() for c in ("High", "Low", "Close", "Volume"))
    hh = df["High"].rolling(lookback).max().shift(1).to_numpy()
    ll = df["Low"].rolling(lookback).min().shift(1).to_numpy()
    atr = ind.atr(df, 14).to_numpy()
    vavg = df["Volume"].rolling(10).mean().shift(1).to_numpy()
    close_t = df.index + BAR
    mins = (close_t.hour * 60 + close_t.minute).to_numpy()
    out = []
    for i in range(lookback + 15, len(df) - 1):
        up = cl[i] > hh[i] and not (cl[i - 1] > hh[i - 1])
        dn = cl[i] < ll[i] and not (cl[i - 1] < ll[i - 1])
        if not (up or dn) or not (atr[i] > 0):
            continue
        side = "CE" if up else "PE"
        if "vol" in filters and not (vavg[i] > 0 and vol[i] >= 2 * vavg[i]):
            continue
        if "room60" in filters and room[i] == room[i] and room[i] >= 0.60:
            continue
        if "room50" in filters and room[i] == room[i] and room[i] >= 0.50:
            continue
        if "funding" in filters:
            fr = fund.iloc[i]
            if fr == fr and ((side == "CE" and fr > 0.0003) or (side == "PE" and fr < -0.0003)):
                continue
        if "us" in filters and not (18 * 60 + 30 <= mins[i] <= 23 * 60 + 30):
            continue
        if "no_asia" in filters and (9 * 60 <= mins[i] < 14 * 60):
            continue
        stop = cl[i] - atr_mult * atr[i] if up else cl[i] + atr_mult * atr[i]
        target = cl[i] + rr * (cl[i] - stop) if up else cl[i] - rr * (stop - cl[i])
        out.append((i, side, cl[i], stop, target))
    return out


def run_breakout(df, entries):
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    out = []
    for i, side, entry, stop, target in entries:
        px, j, via = walk_simple(hi, lo, cl, i, side, stop, target, max_bars=96)
        out.append({"when": df.index[i] + BAR, "exit_time": df.index[j] + BAR, "side": side,
                    "entry": entry, "exit": px, "i": i, "j": j})
    return sequential(out)


# ---------------------------------------------------------------- candidate C: 4h trend following
def run_trend(df):
    h4 = df.resample("4h", origin="epoch").agg({"Open": "first", "High": "max", "Low": "min",
                                                   "Close": "last"}).dropna()
    hi, lo, cl = h4["High"].to_numpy(), h4["Low"].to_numpy(), h4["Close"].to_numpy()
    hh20 = h4["High"].rolling(20).max().shift(1).to_numpy()
    ll20 = h4["Low"].rolling(20).min().shift(1).to_numpy()
    hh10 = h4["High"].rolling(10).max().shift(1).to_numpy()
    ll10 = h4["Low"].rolling(10).min().shift(1).to_numpy()
    atr = ind.atr(h4, 14).to_numpy()
    out, i, n = [], 35, len(h4)
    while i < n - 1:
        up, dn = cl[i] > hh20[i], cl[i] < ll20[i]
        if not (up or dn) or not (atr[i] > 0):
            i += 1
            continue
        ce = bool(up)
        stop = cl[i] - 2 * atr[i] if ce else cl[i] + 2 * atr[i]
        px, j = None, None
        for k in range(i + 1, n):
            if (lo[k] <= stop) if ce else (hi[k] >= stop):
                px, j = stop, k
                break
            if (cl[k] < ll10[k]) if ce else (cl[k] > hh10[k]):
                px, j = cl[k], k
                break
        if j is None:
            break
        out.append({"when": h4.index[i] + pd.Timedelta(hours=4), "exit_time": h4.index[j] + pd.Timedelta(hours=4),
                    "side": "CE" if ce else "PE", "entry": cl[i], "exit": px})
        i = j + 1
    return out


# ---------------------------------------------------------------- pricing a trade list
def price_all(rows, df, iv):
    """Adds perp / option P&L columns in place. iv and df are read at the entry
    and exit bar closes (the entry/exit stamps are already bar CLOSE times)."""
    close_t = df.index + BAR
    pos_close = {t: n for n, t in enumerate(close_t)}
    cl = df["Close"].to_numpy()
    ivs = iv.to_numpy()

    def at(t):
        k = pos_close.get(t)
        if k is None:
            k = int(np.searchsorted(close_t, t, side="right")) - 1
        return max(0, min(k, len(cl) - 1))

    for r in rows:
        a, b = at(r["when"]), at(r["exit_time"])
        iv0, iv1 = ivs[a], ivs[b]
        r["perp"] = price_perp(r["side"], r["entry"], r["exit"])
        r["perp_maker"] = price_perp(r["side"], r["entry"], r["exit"], fee=FEE_PERP_MAKER)
        r["gross"] = (1 if r["side"] == "CE" else -1) * (r["exit"] - r["entry"])
        for pol in ("nearest", "next_day", "monthly"):
            if not (iv0 == iv0 and iv1 == iv1):
                r[f"opt_{pol}"] = None
                continue
            exp = expiry_for(r["when"], pol)
            settle = cl[at(exp)] if r["exit_time"] >= exp else None
            pnl, p0 = price_option(r["side"], r["entry"], r["when"], r["exit"], r["exit_time"],
                                   iv0, iv1, pol, settle_spot=settle)
            r[f"opt_{pol}"] = pnl
            r[f"prem_{pol}"] = p0
    return rows


def main():
    print("Loading BTC history, DVOL and funding...")
    df, iv, fund = load()
    print(f"  {len(df):,} bars {df.index.min():%Y-%m-%d} -> {df.index.max():%Y-%m-%d %H:%M}")
    pre = bt.precompute(df, KEY)
    adx = pre["adx"].to_numpy()
    st_len, st_mult = config.supertrend_params(KEY)
    st_line = ind.supertrend(df, st_len, st_mult)[0].to_numpy()
    room = utc_day_room(df)

    print("Replaying the live rule engine (cached after the first run)...")
    bias, opp = cached(f"btc_bar_bias_{len(df)}.pkl", lambda: res.bar_bias(KEY, df))
    entries_20 = live_entries(df, 2.0, "rr20")
    entries_10 = live_entries(df, 1.0, "rr10")

    KEYS = ("gross", "perp", "perp_maker", "opt_next_day", "opt_monthly")
    S = {}
    # The live engine: walked in index points for the perp/gross columns, and walked
    # on the option's own premium (exactly as live tracks it) for the option columns.
    for name, ents, kw in (
            ("A  live rules (today)", entries_20, {}),
            ("A1 live, reward:risk 1.0", entries_10, {}),
            ("A2 live, no reversal exit", entries_20, {"reversal": False}),
            ("A3 live, no Supertrend trail", entries_20, {"trail": False}),
            ("A4 live, no 2h breakeven", entries_20, {"be_bars": 0}),
            ("A5 live, exit at T3 not T2", entries_20, {"exit_key": "t3"}),
            ("A6 live, no breakeven + T3", entries_20, {"be_bars": 0, "exit_key": "t3"})):
        idx = price_all(run_live(df, iv, ents, opp, st_line, adx, **kw), df, iv)
        S[name] = {"gross": idx, "perp": idx, "perp_maker": idx,
                   "opt_next_day": run_live_premium(df, iv, ents, opp, st_line, adx, "next_day", **kw),
                   "opt_monthly": run_live_premium(df, iv, ents, opp, st_line, adx, "monthly", **kw),
                   "_index": idx}
    other = {"B  breakout 20 (2R)": run_breakout(df, breakout_entries(df, fund, room))}
    for name, flt in (("B+vol", ("vol",)), ("B+room60", ("room60",)), ("B+room50", ("room50",)),
                      ("B+funding", ("funding",)), ("B+us session", ("us",)), ("B+no asia", ("no_asia",)),
                      ("B paste-1 (vol,room60,fund)", ("vol", "room60", "funding")),
                      ("B paste-2 (room50,us)", ("room50", "us"))):
        other[name] = run_breakout(df, breakout_entries(df, fund, room, filters=flt))
    other["C  4h Donchian trend"] = run_trend(df)
    for name, rows in other.items():
        price_all(rows, df, iv)
        S[name] = {k: rows for k in KEYS}
        S[name]["_index"] = rows

    def cell(rows, key):
        s = stats(rows, key)
        return f"{s['n']:>4} {s['total']:>+9,.0f} PF{s['pf']:>5.2f}"

    print("\n" + "=" * 150)
    print(" $ per 1 BTC of underlying (x0.25 for the usual 250 lots), after Delta fees + 18% GST"
          f" (+ {SPREAD_RT:.0%} option spread round trip). n, total, profit factor.")
    print("=" * 150)
    for key, label in (("gross", "GROSS INDEX POINTS - no costs at all (is there any edge?)"),
                       ("perp", "PERPETUAL, taker fees (0.05%/side)"),
                       ("perp_maker", "PERPETUAL, maker fees (0.02%/side - only if resting limits fill)"),
                       ("opt_next_day", "OPTIONS, next-day expiry (live's short contract; A rows premium-tracked as live)"),
                       ("opt_monthly", "OPTIONS, ~40-day expiry (live's long contract; A rows premium-tracked as live)")):
        print(f"\n {label}")
        print(f" {'strategy':32s} {'IN-SAMPLE (Sep23-Aug25)':>26s} {'HELD-OUT (Aug25-Sep26)':>26s}"
              f" {'LIVE WINDOW (11 Sep-2 Oct)':>28s}")
        for name, by in S.items():
            if key == "opt_next_day" and name.startswith("C"):
                continue                     # multi-day holds outlive a next-day contract
            p = periods(by[key])
            print(f" {name:32s} {cell(p['is'], key):>26s} {cell(p['oos'], key):>26s} {cell(p['live'], key):>28s}")

    print("\n BEST BY IN-SAMPLE, CHECKED ON HELD-OUT (picked on the first period only - no peeking)")
    for key in ("perp", "perp_maker", "opt_next_day", "opt_monthly"):
        cand = {n: by for n, by in S.items() if not (key == "opt_next_day" and n.startswith("C"))}
        best = max(cand, key=lambda n: stats(periods(cand[n][key])["is"], key)["total"])
        p = periods(cand[best][key])
        a, b = stats(p["is"], key), stats(p["oos"], key)
        print(f"   {key:13s} {best:32s} in-sample {a['total']:>+9,.0f} PF {a['pf']:.2f}   held-out "
              f"{b['total']:>+9,.0f} PF {b['pf']:.2f}   -> {'PROFITABLE BOTH' if a['total'] > 0 and b['total'] > 0 else 'not profitable both periods'}")

    # The paste's afternoon-theta warning: the SAME live-rule trades on the nearest
    # daily versus the next day's, entries 13:00-17:30 IST only.
    rows = S["A  live rules (today)"]["_index"]
    aft = [r for r in rows if 13 * 60 <= (r["when"].hour * 60 + r["when"].minute) < 17 * 60 + 30]
    p = periods(aft)
    print("\n AFTERNOON ENTRIES (13:00-17:30 IST), live-rule trades: nearest daily vs next-day expiry")
    for key in ("opt_nearest", "opt_next_day"):
        print(f"   {key:14s} in-sample {cell(p['is'], key)}   held-out {cell(p['oos'], key)}")

    # Does the harness reproduce the user's real record? Same days, same rules, the
    # option premium-tracked as live; BEFORE costs, as the live log itself is.
    print("\n HARNESS CHECK vs the user's real record (premium return per trade, BEFORE fees/spread,"
          " as the live log records it)")
    print("   real record:  11-29 Sep  72 trades, +6.6% avg, 69% winners  |  30 Sep-2 Oct  16 trades, -9.3% avg, 19% winners")
    for pol in ("next_day", "monthly"):
        rows = [r for r in S["A  live rules (today)"][f"opt_{pol}"] if r["when"] >= LIVE_FROM]
        for lbl, sub in (("11-29 Sep", [r for r in rows if r["when"] < pd.Timestamp("2026-09-30", tz="Asia/Kolkata")]),
                         ("30 Sep-2 Oct", [r for r in rows if r["when"] >= pd.Timestamp("2026-09-30", tz="Asia/Kolkata")])):
            rets = [r[f"ret_{pol}"] for r in sub]
            if rets:
                print(f"   backtest {pol:9s} {lbl:13s} {len(rets):3d} trades, {np.mean(rets):+5.1f}% avg, "
                      f"{100 * np.mean([x > 0 for x in rets]):3.0f}% winners")
    return S


if __name__ == "__main__":
    main()
