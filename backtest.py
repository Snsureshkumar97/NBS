#!/usr/bin/env python3
"""
backtest.py — 5-year historical accuracy check for the CE/PE signal strategy
================================================================================

Run this on your OWN computer (needs normal internet access — this can't be
run from a locked-down sandbox):

    pip install -r requirements.txt
    python backtest.py

It downloads ~5 years of DAILY price history for Nifty, Bank Nifty and
Sensex from Yahoo Finance (free, no key needed) and backtests a daily-bar
version of the signal strategy: for every historical BUY-CE / BUY-PE signal,
it checks whether the target or stop-loss was hit first over the following
N trading days, and reports a win rate per index.

This script runs BOTH the OLD strategy (EMA trend + Supertrend + RSI,
fixed ATR-multiple target/SL) and the NEW strategy (EMA trend + MACD
momentum + RSI, with an ADX trend-strength gate and a swing-based
stop-loss/R-multiple target model) over the SAME historical data, and
prints them side by side — so you can see for yourself whether the change
actually helped before trusting it, instead of taking my word for it.

*** WHAT THIS CAN AND CANNOT TEST — read this first ***

Of the two fixes made to the "no room / NOT WORTH IT" rejection:

  * The STOP-WIDTH CAP (MAX_RISK_ATR_MULT) IS tested here. It applies to the
    NEW variant below, and the summary reports how many trades were capped.
    Raise MAX_RISK_ATR_MULT to a huge number and re-run to see the difference.

  * The TREND-DAY RANGE EXPANSION is NOT tested here, and cannot be. This
    backtest has no option chain, so it has no expected move, no OI walls, and
    therefore no reachability calculation at all — it uses plain R-multiple
    targets instead. There is no free 5-year historical NSE option-chain
    archive to build one from.

    The honest check available for that half is `--measure-expansion`, which
    measures from real history whether high-ADX days genuinely range further,
    and by how much. That validates the ASSUMPTION behind the fix. It does not
    prove the fix makes money.

Do not read a good number below as evidence that the range-expansion change
was correct. It says nothing about it either way.

*** READ THIS BEFORE TRUSTING ANY NUMBER THIS PRINTS ***

1. This backtests INDEX-LEVEL price target/stop-loss hits — i.e. "did Nifty
   itself move to the target before the stop, within N days". It does NOT
   simulate actual option premiums (theta decay, IV crush/expansion,
   bid-ask spread, brokerage). A real option trade's P&L can differ
   significantly (usually worse) from the index-level result, especially
   if the move is slow. Treat this win rate as an OPTIMISTIC UPPER BOUND
   on how the underlying strategy's *direction calls* would have fared —
   not as the return you'd actually have made trading options.

2. It uses DAILY candles (5 years of free intraday data isn't available
   from any free source), so this tests a swing-style version of the
   strategy, not the 5m/15m intraday version in main.py. VWAP is dropped
   (needs intraday volume) and OI/PCR is dropped (no free 5-year historical
   NSE option-chain archive exists) — so this backtests the trend +
   momentum + RSI component only, on a daily bar.

3. Past performance on 5 years of history does NOT guarantee future
   results — regimes change, and a rule that worked 2021-2026 may not work
   2026-2031. Use this as one data point, not a guarantee.

4. Small differences in Yahoo's free daily data vs Zerodha/NSE's official
   data are possible (adjustments, minor timestamp differences) — this is
   a research/estimation tool, not a certified backtest.
"""

import sys
import datetime as dt
import numpy as np
import pandas as pd
import requests

import indicators as ind

INSTRUMENTS = {
    "NIFTY": {"yahoo_ticker": "^NSEI", "strike_step": 50},
    "BANKNIFTY": {"yahoo_ticker": "^NSEBANK", "strike_step": 100},
    "SENSEX": {"yahoo_ticker": "^BSESN", "strike_step": 100},
}

# ---------------------------- Strategy settings (daily-bar variant) --------
# Shared by both variants:
EMA_FAST = 20
EMA_SLOW = 50
RSI_LENGTH = 14
RSI_BULL_MIN = 50
RSI_BEAR_MAX = 50
RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25
ATR_LENGTH = 14
# A swing-based stop (NEW variant) sits at a real recent high/low, which is
# usually a wider distance than a small fixed ATR multiple (OLD variant) —
# so the same R-multiple target is a bigger price move and needs more time
# to resolve either way. 25 trading days (~5 weeks) gives both variants a
# fair, apples-to-apples window; 15 days was calibrated for the OLD
# variant's tighter ATR-based stops and unfairly penalizes NEW with
# "time exits" that would have resolved as real wins/losses given more time.
MAX_HOLD_DAYS = 25
MIN_BARS_BETWEEN_SIGNALS = 5  # avoid re-signaling every single day in a trend
SIGNAL_SCORE_THRESHOLD = 2     # out of max 3 — SAME threshold for both variants,
                                 # so this isolates the effect of the 3 things that
                                 # actually changed (see below), not the threshold.

# OLD variant — what the tool used before this update (trend + Supertrend + RSI,
# fixed ATR-multiple target/SL).
SUPERTREND_ATR_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
TARGET_ATR_MULTS = [0.75, 1.5, 2.5]   # T1, T2, T3
SL_ATR_MULT = 0.75

# NEW variant — trend + MACD momentum + RSI (MACD replaces the redundant
# Supertrend vote), gated by ADX trend-strength, with a swing-based
# stop-loss and R-multiple targets. Mirrors config.py exactly.
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ADX_LENGTH = 14
ADX_TREND_THRESHOLD = 20
SL_BUFFER_ATR_MULT = 0.15
MIN_RISK_ATR_MULT = 0.5      # floor on risk (R) — see config.py for rationale
# Ceiling on risk. A swing sitting a very long way from price is not a stop
# anyone would actually take, so beyond this multiple of ATR the stop is placed
# here instead. This is the ONE half of the "no room" fix that this backtest can
# genuinely test — the trend-day range expansion cannot be tested here at all,
# because reachability needs an option chain and no free 5-year historical
# option-chain archive exists. Use `--measure-expansion` for that half.
MAX_RISK_ATR_MULT = 2.0
RR_MULTS = [1.0, 2.0, 3.0]   # T1, T2, T3 as multiples of risk

# ---------------------------------------------------------------------------
# IMPORTANT — why this differs from config.py's SWING_LOOKBACK of 12
# ---------------------------------------------------------------------------
# config.py's 12 is calibrated for the 15-MINUTE candles the live tool runs
# on, where 12 candles is about 3 hours of price action and the resulting
# stop sits roughly 0.3% below entry. This backtest runs on DAILY candles,
# where 12 candles means 12 DAYS and the stop lands about 2.4% below entry —
# eight times wider in relative terms.
#
# That matters enormously, because targets are multiples of the stop
# distance. A 2R target off a 2.4% stop needs a ~5% index move; off a 0.3%
# stop it needs ~0.6%. The first almost never resolves inside the hold
# window, the second is an ordinary intraday move. Testing the daily bars
# with lookback 12 therefore does NOT test the strategy as it actually runs
# — it tests a far more demanding version of it.
#
# 5 daily bars (one trading week) gives a stop distance in the same ballpark
# as the ATR-based one, making the OLD vs NEW comparison apples-to-apples.
# Use --sweep to see how sensitive the results are to this choice.
SWING_LOOKBACK = 5


def fetch_daily_history(ticker: str, years: int = 5) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "range": f"{years}y"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("chart", {}).get("result")
    if not result:
        raise RuntimeError(f"No data for {ticker}: {data.get('chart', {}).get('error')}")
    result = result[0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame(
        {
            "Open": quote["open"],
            "High": quote["high"],
            "Low": quote["low"],
            "Close": quote["close"],
            "Volume": quote["volume"],
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata"),
    )
    df = df.dropna(subset=["Close"])
    return df


def compute_daily_scores(df: pd.DataFrame, variant: str = "old") -> pd.DataFrame:
    close = df["Close"]
    ema_fast = ind.ema(close, EMA_FAST)
    ema_slow = ind.ema(close, EMA_SLOW)
    rsi = ind.rsi(close, RSI_LENGTH)
    atr = ind.atr(df, ATR_LENGTH)

    trend_score = pd.Series(0, index=df.index)
    trend_score[(close > ema_slow) & (ema_fast > ema_slow)] = 1
    trend_score[(close < ema_slow) & (ema_fast < ema_slow)] = -1

    rsi_score = pd.Series(0, index=df.index)
    rsi_score[(rsi > RSI_BULL_MIN) & (rsi < RSI_OVERBOUGHT)] = 1
    rsi_score[(rsi < RSI_BEAR_MAX) & (rsi > RSI_OVERSOLD)] = -1

    out = df.copy()
    out["ATR"] = atr

    if variant == "old":
        st_line, st_dir = ind.supertrend(df, SUPERTREND_ATR_PERIOD, SUPERTREND_MULTIPLIER)
        st_score = st_dir.map(lambda d: 1 if d == 1 else -1)
        out["Score"] = trend_score + st_score + rsi_score
    else:
        _, _, macd_hist = ind.macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        macd_score = pd.Series(0, index=df.index)
        macd_score[macd_hist > 0] = 1
        macd_score[macd_hist < 0] = -1
        out["Score"] = trend_score + macd_score + rsi_score
        out["ADX"] = ind.adx(df, ADX_LENGTH)
        out["SwingLow"] = df["Low"].rolling(SWING_LOOKBACK).min()
        out["SwingHigh"] = df["High"].rolling(SWING_LOOKBACK).max()

    return out


def backtest_index(index_key: str, df: pd.DataFrame, variant: str = "old") -> dict:
    df = compute_daily_scores(df, variant=variant)
    n = len(df)

    trades = []
    last_signal_idx = -10 ** 9

    warmup = max(EMA_SLOW, ATR_LENGTH, SUPERTREND_ATR_PERIOD, MACD_SLOW, ADX_LENGTH, SWING_LOOKBACK) + 5

    adx_blocked_count = 0
    capped_count = 0        # how often the swing stop was too far and got capped

    for i in range(warmup, n - 1):  # -1 so there's at least 1 future bar
        score = df["Score"].iloc[i]
        if abs(score) < SIGNAL_SCORE_THRESHOLD:
            continue
        if i - last_signal_idx < MIN_BARS_BETWEEN_SIGNALS:
            continue

        if variant == "new":
            adx_val = df["ADX"].iloc[i]
            if pd.isna(adx_val) or adx_val < ADX_TREND_THRESHOLD:
                adx_blocked_count += 1
                last_signal_idx = i  # still counts as "a signal happened here" for spacing
                continue

        side = "CE" if score > 0 else "PE"
        entry_price = df["Close"].iloc[i]
        atr_val = df["ATR"].iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        if variant == "old":
            t1_mult, t2_mult, t3_mult = TARGET_ATR_MULTS
            if side == "CE":
                t1 = entry_price + atr_val * t1_mult
                t2 = entry_price + atr_val * t2_mult
                t3 = entry_price + atr_val * t3_mult
                stop = entry_price - atr_val * SL_ATR_MULT
            else:
                t1 = entry_price - atr_val * t1_mult
                t2 = entry_price - atr_val * t2_mult
                t3 = entry_price - atr_val * t3_mult
                stop = entry_price + atr_val * SL_ATR_MULT
        else:
            r1_mult, r2_mult, r3_mult = RR_MULTS
            min_risk = atr_val * MIN_RISK_ATR_MULT
            max_risk = atr_val * MAX_RISK_ATR_MULT
            if side == "CE":
                swing_low = df["SwingLow"].iloc[i]
                if pd.notna(swing_low) and swing_low < entry_price and (entry_price - swing_low) >= min_risk:
                    stop = swing_low - atr_val * SL_BUFFER_ATR_MULT
                else:
                    stop = entry_price - atr_val * SL_ATR_MULT
                risk = entry_price - stop
                if risk > max_risk:          # swing too far to be a usable stop
                    stop = entry_price - max_risk
                    risk = max_risk
                    capped_count += 1
                if risk <= 0:
                    stop = entry_price - atr_val * SL_ATR_MULT
                    risk = entry_price - stop
                t1 = entry_price + risk * r1_mult
                t2 = entry_price + risk * r2_mult
                t3 = entry_price + risk * r3_mult
            else:
                swing_high = df["SwingHigh"].iloc[i]
                if pd.notna(swing_high) and swing_high > entry_price and (swing_high - entry_price) >= min_risk:
                    stop = swing_high + atr_val * SL_BUFFER_ATR_MULT
                else:
                    stop = entry_price + atr_val * SL_ATR_MULT
                risk = stop - entry_price
                if risk > max_risk:
                    stop = entry_price + max_risk
                    risk = max_risk
                    capped_count += 1
                if risk <= 0:
                    stop = entry_price + atr_val * SL_ATR_MULT
                    risk = stop - entry_price
                t1 = entry_price - risk * r1_mult
                t2 = entry_price - risk * r2_mult
                t3 = entry_price - risk * r3_mult

        # Primary outcome (win/loss/time-exit) is defined against T2, same
        # convention as the original single-target version, for consistent
        # headline win-rate reporting. T1/T3 are resolved independently
        # below purely to report "how often would each tier have been
        # reached before stop" — they don't affect the primary outcome.
        outcome, exit_bar_offset, exit_price = _resolve_level(df, i, side, t2, stop)
        t1_outcome, _, _ = _resolve_level(df, i, side, t1, stop)
        t3_outcome, _, _ = _resolve_level(df, i, side, t3, stop)

        trades.append(
            {
                "date": df.index[i].date(),
                "side": side,
                "entry": round(entry_price, 2),
                "t1": round(t1, 2),
                "t2": round(t2, 2),
                "t3": round(t3, 2),
                "stop": round(stop, 2),
                "outcome": outcome,
                "t1_outcome": t1_outcome,
                "t3_outcome": t3_outcome,
                "days_to_resolve": exit_bar_offset,
                "exit_price": round(exit_price, 2),
                "stop_pct": round(abs(entry_price - stop) / entry_price * 100, 2),
                "r_multiple": round(
                    (exit_price - entry_price) / (entry_price - stop) if side == "CE"
                    else (entry_price - exit_price) / (stop - entry_price),
                    2,
                ),
            }
        )
        last_signal_idx = i

    res = summarize(index_key, trades)
    res["variant"] = variant
    res["adx_blocked_count"] = adx_blocked_count
    res["capped_count"] = capped_count
    return res


def _resolve_level(df: pd.DataFrame, entry_idx: int, side: str, target: float, stop: float):
    """Look forward up to MAX_HOLD_DAYS bars for ONE target level. If both
    the target & stop are touched within the same bar, conservatively
    assume the STOP was hit first (worst-case assumption, since we don't
    have intraday sequencing from daily bars)."""
    n = len(df)
    end_idx = min(entry_idx + MAX_HOLD_DAYS, n - 1)

    for j in range(entry_idx + 1, end_idx + 1):
        high = df["High"].iloc[j]
        low = df["Low"].iloc[j]

        if side == "CE":
            hit_target = high >= target
            hit_stop = low <= stop
        else:
            hit_target = low <= target
            hit_stop = high >= stop

        if hit_target and hit_stop:
            return "LOSS (SL, conservative same-bar assumption)", j - entry_idx, stop
        if hit_target:
            return "WIN", j - entry_idx, target
        if hit_stop:
            return "LOSS", j - entry_idx, stop

    # Time-based exit at close of last available bar in the window
    exit_price = df["Close"].iloc[end_idx]
    return "TIME EXIT", end_idx - entry_idx, exit_price


def summarize(index_key: str, trades: list) -> dict:
    if not trades:
        return {"index": index_key, "total_trades": 0}

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"].startswith("LOSS")]
    time_exits = [t for t in trades if t["outcome"] == "TIME EXIT"]
    time_exit_wins = [t for t in time_exits if t["r_multiple"] > 0]

    total = len(trades)
    strict_win_rate = round(100 * len(wins) / total, 1)
    inclusive_wins = len(wins) + len(time_exit_wins)
    inclusive_win_rate = round(100 * inclusive_wins / total, 1)

    t1_hit_rate = round(100 * sum(1 for t in trades if t["t1_outcome"] == "WIN") / total, 1)
    t2_hit_rate = strict_win_rate  # T2 IS the primary target
    t3_hit_rate = round(100 * sum(1 for t in trades if t["t3_outcome"] == "WIN") / total, 1)

    avg_r = round(sum(t["r_multiple"] for t in trades) / total, 2)
    stops = sorted(t["stop_pct"] for t in trades)
    median_stop_pct = stops[len(stops) // 2]
    # How big a move each target actually demands, in % of index price.
    # This is the number that decides whether a target is reachable at all.
    t2_move_pct = round(median_stop_pct * 2.0, 2)

    # max consecutive losses (LOSS outcomes only, ignoring time exits)
    max_consec_loss = 0
    cur = 0
    for t in trades:
        if t["outcome"].startswith("LOSS"):
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    return {
        "index": index_key,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "time_exits": len(time_exits),
        "time_exit_wins": len(time_exit_wins),
        "strict_win_rate_pct": strict_win_rate,      # target hit cleanly, excludes time exits from numerator
        "inclusive_win_rate_pct": inclusive_win_rate, # + time exits that were net positive
        "t1_hit_rate_pct": t1_hit_rate,
        "t2_hit_rate_pct": t2_hit_rate,
        "t3_hit_rate_pct": t3_hit_rate,
        "avg_r_multiple": avg_r,
        "max_consecutive_losses": max_consec_loss,
        "median_stop_pct": median_stop_pct,
        "t2_move_pct": t2_move_pct,
        "trades": trades,
    }


def print_summary(res: dict):
    variant_label = "OLD strategy (trend+Supertrend+RSI, ATR target/SL)" if res.get("variant") == "old" \
        else "NEW strategy (trend+MACD+RSI, ADX-gated, swing SL, R-multiple targets)"
    print("=" * 78)
    print(f" {res['index']} — 5-Year Daily-Bar Backtest — {variant_label}")
    print("=" * 78)
    if res["total_trades"] == 0:
        print("No signals generated in this window.")
        if res.get("adx_blocked_count"):
            print(f"({res['adx_blocked_count']} would-be signals were blocked by the ADX trend-strength filter.)")
        print()
        return
    print(f"Total signals              : {res['total_trades']}")
    if res.get("variant") == "new" and res.get("adx_blocked_count"):
        print(f"  (+ {res['adx_blocked_count']} more would-be signals blocked by the ADX trend-strength filter)")
    if res.get("variant") == "new" and res.get("capped_count") is not None:
        n_tr = len(res.get("trades", [])) or 1
        print(f"  ({res['capped_count']} of {n_tr} trades had a swing stop further than "
              f"{MAX_RISK_ATR_MULT}x ATR and were capped — set MAX_RISK_ATR_MULT high to "
              f"disable and re-run to compare)")
    print(f"Wins (target hit cleanly)  : {res['wins']}")
    print(f"Losses (stop hit)          : {res['losses']}")
    print(f"Time exits (15-day cap)    : {res['time_exits']}  (of which net positive: {res['time_exit_wins']})")
    print(f"STRICT win rate (T2)       : {res['strict_win_rate_pct']}%   (target hit / total)")
    print(f"INCLUSIVE win rate         : {res['inclusive_win_rate_pct']}%   (target hit + net-positive time exits / total)")
    print("-" * 78)
    if res.get("variant") == "old":
        print("3-TIER TARGET HIT RATES (independent — % of signals that reached each tier before stop):")
        print(f"    T1 (ATR x{TARGET_ATR_MULTS[0]}) : {res['t1_hit_rate_pct']}%")
        print(f"    T2 (ATR x{TARGET_ATR_MULTS[1]}) : {res['t2_hit_rate_pct']}%")
        print(f"    T3 (ATR x{TARGET_ATR_MULTS[2]}) : {res['t3_hit_rate_pct']}%")
    else:
        print("3-TIER TARGET HIT RATES (independent — % of signals that reached each tier before stop):")
        print(f"    T1 ({RR_MULTS[0]}R) : {res['t1_hit_rate_pct']}%")
        print(f"    T2 ({RR_MULTS[1]}R) : {res['t2_hit_rate_pct']}%")
        print(f"    T3 ({RR_MULTS[2]}R) : {res['t3_hit_rate_pct']}%")
    print("-" * 78)
    print(f"Average R multiple/trade   : {res['avg_r_multiple']}   (>0 means net profitable in R terms, based on T2)")
    print(f"Max consecutive losses     : {res['max_consecutive_losses']}")
    print(f"Median stop distance       : {res['median_stop_pct']}% of index price")
    print(f"  -> T2 therefore needs a  : {res['t2_move_pct']}% index move to be reached")
    if res["t2_move_pct"] > 3.0:
        print("     ^^ that is a LARGE move for this hold window — a low T2 hit rate")
        print("        here reflects an unreachable target, not a bad direction call.")
    print("-" * 78)
    print("(Remember: this is index-level target/SL hit rate, NOT real option P&L.")
    print(" See the disclaimer at the top of this script before drawing conclusions.)")
    print()


def print_comparison_table(old_results: dict, new_results: dict):
    print("=" * 78)
    print(" OLD vs NEW STRATEGY — SIDE BY SIDE")
    print("=" * 78)
    header = f"{'Index':<11}{'Variant':<6}{'Signals':<9}{'Strict win%':<13}{'Inclusive win%':<16}{'Avg R':<8}{'Max consec loss'}"
    print(header)
    print("-" * 78)
    for index_key in old_results:
        o, n = old_results.get(index_key), new_results.get(index_key)
        if o and o.get("total_trades"):
            print(f"{index_key:<11}{'OLD':<6}{o['total_trades']:<9}{o['strict_win_rate_pct']:<13}"
                  f"{o['inclusive_win_rate_pct']:<16}{o['avg_r_multiple']:<8}{o['max_consecutive_losses']}")
        else:
            print(f"{index_key:<11}{'OLD':<6}(no signals)")
        if n and n.get("total_trades"):
            blocked = f"  [+{n.get('adx_blocked_count', 0)} ADX-blocked]" if n.get("adx_blocked_count") else ""
            print(f"{'':<11}{'NEW':<6}{n['total_trades']:<9}{n['strict_win_rate_pct']:<13}"
                  f"{n['inclusive_win_rate_pct']:<16}{n['avg_r_multiple']:<8}{n['max_consecutive_losses']}{blocked}")
        else:
            blocked = f"  ({n.get('adx_blocked_count', 0)} ADX-blocked)" if n and n.get("adx_blocked_count") else ""
            print(f"{'':<11}{'NEW':<6}(no signals){blocked}")
        print()
    print("Fewer NEW signals than OLD is expected — the ADX filter is deliberately")
    print("trading fewer, higher-conviction setups instead of more, noisier ones.")
    print("Judge by win rate / avg R, not raw signal count.")
    print("=" * 78)
    print()


def run_sweep(dfs: dict):
    """Diagnostic: how sensitive is the NEW strategy to the swing-lookback
    and target settings?

    *** THIS IS NOT A TUNING TOOL. *** If you run a dozen combinations and
    then trade whichever scored best, you have curve-fitted to five years of
    history and learned nothing about the future — that is the single most
    common way backtests mislead people. Use this only to see WHETHER the
    result is stable. A strategy that only works at one exact setting has no
    real edge; one that is broadly positive across many settings might.
    """
    global SWING_LOOKBACK, RR_MULTS
    original_lb, original_rr = SWING_LOOKBACK, RR_MULTS

    print("=" * 78)
    print(" SENSITIVITY SWEEP — NEW strategy across settings")
    print("=" * 78)
    print("Read this for STABILITY, not for the best score. See the warning in the")
    print("source. A result that flips from good to bad on a small setting change")
    print("is noise, not an edge.")
    print()
    print(f"{'Swing':<7}{'Targets':<14}{'Signals':<9}{'Strict%':<9}{'Avg R':<8}{'Stop%':<8}{'T2 needs'}")
    print("-" * 78)

    for lb in (3, 5, 8, 12):
        for rr in ([1.0, 2.0, 3.0], [0.5, 1.0, 1.5]):
            SWING_LOOKBACK, RR_MULTS = lb, rr
            agg_trades, agg_signals = [], 0
            for key, df in dfs.items():
                res = backtest_index(key, df, variant="new")
                if res.get("total_trades"):
                    agg_trades.extend(res["trades"])
                    agg_signals += res["total_trades"]
            if not agg_trades:
                print(f"{lb:<7}{str(rr):<14}(no signals)")
                continue
            wins = sum(1 for t in agg_trades if t["outcome"] == "WIN")
            avg_r = sum(t["r_multiple"] for t in agg_trades) / len(agg_trades)
            stops = sorted(t["stop_pct"] for t in agg_trades)
            med_stop = stops[len(stops) // 2]
            print(f"{lb:<7}{str(rr):<14}{agg_signals:<9}{round(100*wins/agg_signals,1):<9}"
                  f"{round(avg_r,3):<8}{med_stop:<8}{round(med_stop*rr[1],2)}%")

    SWING_LOOKBACK, RR_MULTS = original_lb, original_rr
    print()


def print_winrate_tradeoff(dfs: dict, cost_in_r: float = 0.10):
    """The single most important table in this file.

    It answers: "can I get a 90% win rate?" — and shows what that costs.

    You CAN have almost any win rate you like. Put the target close enough to
    entry and you will hit it nearly every time. What you cannot do is have a
    high win rate AND a big win, because the two are mechanically linked:
    roughly, the chance of touching a target before the stop is

        stop_distance / (stop_distance + target_distance)

    Want 90%? Then the target has to sit about a NINTH of the way to the
    stop. You win nine small amounts, then give it all back on the tenth
    loss. The arithmetic cancels almost exactly to zero — before costs. After
    brokerage, the bid-ask spread on the option, and time decay, it is
    negative.

    `cost_in_r` is a rough round-trip trading cost expressed as a fraction of
    the amount you risk per trade. 0.10 means costs eat 10% of your risk each
    trade, which is a realistic ballpark for index options at typical size.
    """
    print("=" * 78)
    print(" THE WIN-RATE TRADE-OFF — read this before chasing a high win rate")
    print("=" * 78)
    print("Each row exits the WHOLE position at T1. Nothing changes except how far")
    print(f"away T1 sits. Costs assumed at {cost_in_r:.2f}R round trip per trade.")
    print()
    print(f"{'T1 distance':<14}{'Win rate':<11}{'Profit/trade':<15}{'After costs':<14}{'Verdict'}")
    print("-" * 78)

    global RR_MULTS
    original = RR_MULTS
    rows = []
    for t1 in (0.10, 0.15, 0.25, 0.40, 0.60, 1.00, 1.50, 2.00, 3.00):
        RR_MULTS = [t1, t1, t1]          # exit fully at T1
        wins = losses = other = 0
        for key, df in dfs.items():
            res = backtest_index(key, df, variant="new")
            for t in res.get("trades", []):
                if t["outcome"] == "WIN":
                    wins += 1
                elif t["outcome"].startswith("LOSS"):
                    losses += 1
                else:
                    other += 1
        total = wins + losses + other
        if not total:
            continue
        wr = wins / total
        # Expectancy in R: a win pays t1 R, a stop-out costs 1R, a time exit
        # is treated as roughly flat (it is neither, and is a small share).
        exp_r = wr * t1 - (losses / total) * 1.0
        net = exp_r - cost_in_r
        verdict = "PROFITABLE" if net > 0.02 else ("break-even" if net > -0.02 else "LOSES money")
        rows.append((t1, wr * 100, exp_r, net, verdict))
        print(f"{str(t1) + 'R':<14}{wr*100:>6.1f}%    {exp_r:>+8.3f}R      "
              f"{net:>+8.3f}R     {verdict}")
    RR_MULTS = original

    print("-" * 78)
    high = [r for r in rows if r[1] >= 85]
    good = [r for r in rows if r[3] > 0]
    if high:
        best_high = max(high, key=lambda r: r[3])
        print(f"Highest win rates here reach {max(r[1] for r in high):.0f}% — and the best of")
        print(f"them still nets {best_high[3]:+.3f}R per trade after costs.")
    if good:
        b = max(good, key=lambda r: r[3])
        print(f"Best net result: T1 at {b[0]}R, {b[1]:.1f}% win rate, {b[3]:+.3f}R after costs.")
    else:
        print("NO setting in this table is profitable after costs. That is the honest")
        print("answer: the edge is not there to be found by moving the target.")
    print()
    print("The lesson: win rate on its own is a vanity number. A 90% win rate with")
    print("a tiny target loses money. A 35% win rate with a big target can make it.")
    print("What matters is win rate MULTIPLIED by size of win, minus costs.")
    print("=" * 78)
    print()


def print_verdict(old_results: dict, new_results: dict):
    """Turn the numbers into a plain-language answer to the only question
    that matters: does either of these actually make money?"""
    print("=" * 78)
    print(" WHAT THIS ACTUALLY MEANS")
    print("=" * 78)

    for label, results in (("OLD", old_results), ("NEW", new_results)):
        trades = [t for r in results.values() for t in r.get("trades", [])]
        if not trades:
            continue
        avg_r = sum(t["r_multiple"] for t in trades) / len(trades)
        print(f"\n{label} strategy: {len(trades)} trades, average {avg_r:+.3f}R per trade")
        if avg_r <= 0:
            print("  -> LOSES money before any costs. Do not trade this.")
        elif avg_r < 0.15:
            print("  -> Roughly BREAK-EVEN before costs. Once you subtract brokerage,")
            print("     STT, the option bid-ask spread, and time decay on a multi-day")
            print("     hold, this is very likely a LOSING strategy in practice.")
        elif avg_r < 0.3:
            print("  -> Slight positive edge before costs. Costs could still erase it.")
            print("     Worth paper-trading; not worth betting size on yet.")
        else:
            print("  -> Meaningful positive edge in index terms. Still verify on real")
            print("     option premiums before trusting it with money.")

    print()
    print("-" * 78)
    print("THE BIGGEST CAVEAT, WORTH REPEATING:")
    print("  These numbers track the INDEX hitting a level. You trade OPTIONS.")
    print("  An option loses value every day it is held, even when the index goes")
    print("  your way. A trade that takes 10 days to reach target here can still")
    print("  be a LOSS in your account. So treat every number above as the best")
    print("  case, not the expected case.")
    print("-" * 78)
    print()


def measure_range_expansion(dfs: dict):
    """Do trending days actually have bigger ranges — and by how much?

    The tool assumes they do, and scales its "how much of a normal day's range
    is left" limit accordingly (RANGE_EXPANSION_BY_ADX in config.py). Those
    numbers should not be taken on trust. This measures the real thing from
    real history: for every day, take ADX as it stood at the PREVIOUS close
    (so nothing is known that wouldn't have been known at the time), then
    compare that day's actual range against the trailing average range.

    A ratio of 1.6 in the ADX 25-35 bucket means: on those days the market
    really did travel about 1.6x a normal day.

    Caveat worth keeping in mind — this is measured on DAILY bars, while the
    live tool reads ADX on 15-minute bars. The direction of the effect should
    carry across; the exact multiple may not. It is evidence, not proof.
    """
    import indicators as ind

    print("=" * 78)
    print(" DOES A TRENDING DAY ACTUALLY RANGE FURTHER?")
    print("=" * 78)
    print(" ADX measured at the prior close. Ratio = that day's range vs the")
    print(" trailing 20-day average range. This is what config.py's")
    print(" RANGE_EXPANSION_BY_ADX should be set from.\n")

    buckets = [(0, 20), (20, 25), (25, 35), (35, 999)]
    pooled = {b: [] for b in buckets}

    for index_key, df in dfs.items():
        if df is None or len(df) < 80:
            continue
        adx = ind.adx(df, 14)
        rng = df["High"] - df["Low"]
        avg = rng.rolling(20).mean()
        rows = []
        for b in buckets:
            vals = []
            for i in range(21, len(df)):
                a = float(adx.iloc[i - 1])            # yesterday's reading
                base = float(avg.iloc[i - 1])
                if base <= 0 or a != a:
                    continue
                if b[0] <= a < b[1]:
                    vals.append(float(rng.iloc[i]) / base)
            pooled[b].extend(vals)
            rows.append((b, vals))
        print(f" {index_key}")
        for b, vals in rows:
            if vals:
                vals_sorted = sorted(vals)
                med = vals_sorted[len(vals_sorted) // 2]
                print(f"   ADX {b[0]:>3}-{b[1] if b[1] < 999 else '+':<4} "
                      f"n={len(vals):<5} mean {sum(vals)/len(vals):.2f}x   median {med:.2f}x")
            else:
                print(f"   ADX {b[0]:>3}-{b[1] if b[1] < 999 else '+':<4} no days")
        print()

    print(" ALL THREE INDICES POOLED — use these:")
    suggested = []
    for b in buckets:
        vals = pooled[b]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        print(f"   ADX {b[0]:>3}-{b[1] if b[1] < 999 else '+':<4} "
              f"n={len(vals):<6} mean {mean:.2f}x")
        suggested.append((b[0], round(mean, 2)))

    if suggested:
        print("\n Which makes the honest setting:")
        print("   RANGE_EXPANSION_BY_ADX = [", end="")
        print(", ".join(f"({lo}, {m})" for lo, m in sorted(suggested, reverse=True)), end="")
        print("]")
        print("\n Compare that against what's in config.py now. If the measured numbers")
        print(" are LOWER, the expansion is too aggressive and should come down.")
    print("=" * 78 + "\n")


def main():
    years = 5
    do_sweep = False
    only_measure = False
    for arg in sys.argv[1:]:
        if arg == "--sweep":
            do_sweep = True
        elif arg == "--measure-expansion":
            only_measure = True
        else:
            try:
                years = int(arg)
            except ValueError:
                pass

    print(f"Fetching {years} years of daily data and backtesting NIFTY, BANKNIFTY, SENSEX "
          f"(OLD strategy vs NEW strategy)...\n")

    old_results = {}
    new_results = {}
    dfs = {}
    for index_key, meta in INSTRUMENTS.items():
        try:
            df = fetch_daily_history(meta["yahoo_ticker"], years=years)
            print(f"{index_key}: fetched {len(df)} daily candles "
                  f"({df.index[0].date()} to {df.index[-1].date()})")
            dfs[index_key] = df
            if only_measure:
                continue
            old_results[index_key] = backtest_index(index_key, df, variant="old")
            new_results[index_key] = backtest_index(index_key, df, variant="new")
        except Exception as e:
            print(f"{index_key}: FAILED to fetch/backtest — {e}")

    if only_measure:
        print()
        measure_range_expansion(dfs)
        return

    print()
    print(f"(NEW strategy uses SWING_LOOKBACK={SWING_LOOKBACK} for these DAILY bars — "
          f"see the note\n in the source explaining why config.py's 12 would not be a "
          f"fair test here.)\n")

    for index_key in old_results:
        print_summary(old_results[index_key])
        print_summary(new_results[index_key])

    print_comparison_table(old_results, new_results)

    # Combined overall accuracy across all three indices, per variant
    for label, results in (("OLD", old_results), ("NEW", new_results)):
        all_trades = [t for res in results.values() for t in res.get("trades", [])]
        if all_trades:
            wins = sum(1 for t in all_trades if t["outcome"] == "WIN")
            print("=" * 78)
            print(f" COMBINED ({label}, all 3 indices): {len(all_trades)} signals, "
                  f"strict accuracy = {round(100*wins/len(all_trades),1)}%")
            print("=" * 78)

    print()
    print_verdict(old_results, new_results)

    if dfs:
        print_winrate_tradeoff(dfs)
        measure_range_expansion(dfs)

    if do_sweep and dfs:
        run_sweep(dfs)
    elif dfs:
        print("Tip: run `python backtest.py --sweep` to see how sensitive the NEW")
        print("     strategy is to its settings. Read the warning in that section first.")

    print("\nDone. Copy this entire output and share it back if you'd like it turned into a report.")


if __name__ == "__main__":
    main()
