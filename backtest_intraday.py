#!/usr/bin/env python3
"""
backtest_intraday.py — the honest backtest, on the timeframe you actually trade
================================================================================
The other backtest (backtest.py) uses free daily bars from Yahoo. That was
never a test of this strategy — it was a test of a swing-trading cousin of it.
The live tool reads 15-minute candles, and a stop placed at the 12-candle swing
means something completely different on 15-minute bars than on daily ones.

Your Kite Connect subscription includes historical data at no extra cost, and
15-minute candles go back to 2015. So this runs the REAL strategy on the REAL
timeframe:

    python3 backtest_intraday.py                 # 3 years, all three indices
    python3 backtest_intraday.py --years 5
    python3 backtest_intraday.py --index BANKNIFTY
    python3 backtest_intraday.py --no-cache      # re-download

It calls signal_engine.build_recommendation() and signal_engine
.compute_reachability() — the same functions the window calls — rather than a
paraphrase of them. When the strategy changes, this changes with it.

--------------------------------------------------------------------------------
WHAT THIS CAN AND CANNOT TELL YOU
--------------------------------------------------------------------------------
CAN:
  * Real 15-minute candles, the timeframe the tool trades.
  * The real indicator votes, the real ADX gate, the real swing stop and the
    real MAX_RISK_ATR_MULT cap.
  * The real day-range limit, including the trend-day expansion — because
    "how much of a normal day's range is left" is computed from candles alone.
    This is the part backtest.py could not test at all.
  * Intraday resolution: which of the stop or the target was touched FIRST,
    checked bar by bar, with a square-off at the close.

CANNOT:
  * Option premiums. Every number here is INDEX POINTS. A real option trade
    loses to theta while you wait, so a winning index move can still be a
    losing trade. Treat every result as an optimistic upper bound.
  * PCR. Expired option contracts drop out of Kite's instrument list, so there
    is no historical option chain to rebuild. PCR simply abstains throughout —
    which the engine handles correctly (an abstention never raises the bar),
    but it does mean the fifth vote is missing from every signal here.
  * Expected move and OI walls, for the same reason. Of the three limits on
    reachability, only the day-range one is live in this test.
  * Costs. No brokerage, no STT, no slippage, no bid-ask.

So: a good result here is necessary but not sufficient. A bad result is
decisive.
"""

import argparse
import datetime as dt
import os
import sys
import time

import numpy as np
import pandas as pd

import config
import indicators as ind
import signal_engine as se

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CHUNK_DAYS = 180          # under Kite's 200-day cap for 15-minute candles
REQ_SLEEP = 0.40          # Kite allows 3 requests/second
CACHE_DIR = os.path.join(os.path.expanduser("~"), "trading-tool-logs", "history")


# ===========================================================================
# DATA
# ===========================================================================
def _cache_path(index_key, years):
    # Normalised, because argparse only applies type=float to a value actually
    # typed on the command line: the default stays int 3 while "--years 3"
    # arrives as 3.0. Those produced two different filenames for the same three
    # years, so asking for it explicitly re-downloaded and cached a second copy
    # of a 7MB file the tool already had.
    y = float(years)
    tag = str(int(y)) if y == int(y) else str(y)
    return os.path.join(CACHE_DIR, f"{index_key}_15m_{tag}y.csv")


def _fetch_deribit(index_key, years):
    """Page Deribit's chart endpoint backwards.

    It caps a response at about 5,000 bars whatever window you ask for - a
    single call covers roughly seven weeks of 15-minute candles - so three
    years has to be walked in chunks. Measured available back to at least
    early 2021.
    """
    import requests
    meta = config.INSTRUMENTS[index_key]
    inst = meta["deribit_instrument"]
    span = 45 * 86400                       # comfortably inside the 5k cap
    end = int(time.time())
    floor = end - int(years * 365.25 * 86400)
    frames, chunks = [], 0
    while end > floor:
        start = max(end - span, floor)
        try:
            r = requests.get(
                "https://www.deribit.com/api/v2/public/get_tradingview_chart_data",
                params={"instrument_name": inst, "resolution": "15",
                        "start_timestamp": start * 1000,
                        "end_timestamp": end * 1000}, timeout=45).json()
            res = r.get("result") or {}
            ticks = res.get("ticks") or []
            if ticks:
                frames.append(pd.DataFrame({
                    "date": pd.to_datetime(ticks, unit="ms", utc=True),
                    "open": res["open"], "high": res["high"],
                    "low": res["low"], "close": res["close"],
                    "volume": res["volume"]}))
            chunks += 1
            print(f"  {index_key}: chunk {chunks} "
                  f"({dt.datetime.utcfromtimestamp(start):%Y-%m-%d} to "
                  f"{dt.datetime.utcfromtimestamp(end):%Y-%m-%d}) -> {len(ticks):,}")
        except Exception as exc:
            print(f"  {index_key}: chunk failed — {exc}")
        end = start
        time.sleep(0.35)
    if not frames:
        raise RuntimeError(f"No Deribit history returned for {index_key}")
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = (df.drop_duplicates(subset="date").sort_values("date")
            .set_index("date"))
    df.columns = [c.capitalize() for c in df.columns]
    df.index.name = "Date"
    return df


def fetch_history(index_key, years=3, use_cache=True):
    """15-minute candles, paged in 180-day chunks and cached to disk.

    Cached because re-downloading three years of candles every time you want
    to change one constant is the fastest way to stop running the backtest at
    all. Delete ~/trading-tool-logs/history to force a refresh.
    """
    path = _cache_path(index_key, years)
    if use_cache and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        print(f"  {index_key}: {len(df):,} candles from cache "
              f"({df.index[0].date()} to {df.index[-1].date()})")
        return df

    if config.market_for(index_key)["market_provider"] == "deribit":
        df = _fetch_deribit(index_key, years)
        df.to_csv(path)
        print(f"  {index_key}: {len(df):,} candles cached -> {path}")
        return df

    from data_providers import KiteDataProvider
    if not config.KITE_API_KEY or not config.KITE_ACCESS_TOKEN:
        raise RuntimeError(
            "This needs a live Kite login. Open the tool and click 'Login to "
            "Zerodha', or run: python3 kite_auth.py")

    provider = KiteDataProvider(config.KITE_API_KEY, config.KITE_ACCESS_TOKEN)
    token = provider._instrument_token(index_key)

    end = dt.datetime.now()
    start = end - dt.timedelta(days=int(years * 365.25))
    frames, cursor, chunks = [], start, 0
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=CHUNK_DAYS), end)
        try:
            candles = provider.kite.historical_data(token, cursor, stop, "15minute")
            if candles:
                frames.append(pd.DataFrame(candles))
            chunks += 1
            print(f"  {index_key}: chunk {chunks} "
                  f"({cursor.date()} to {stop.date()}) -> {len(candles):,} candles")
        except Exception as exc:
            print(f"  {index_key}: chunk {cursor.date()}-{stop.date()} FAILED — {exc}")
        cursor = stop
        time.sleep(REQ_SLEEP)

    if not frames:
        raise RuntimeError(f"No historical data returned for {index_key}")

    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"date": "Date", "open": "Open", "high": "High",
                            "low": "Low", "close": "Close", "volume": "Volume"})
    df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
    df = df[~df.index.duplicated(keep="first")].sort_index()

    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(path)
    print(f"  {index_key}: {len(df):,} candles saved to {path}")
    return df


# ===========================================================================
# PRECOMPUTE
# ===========================================================================
def precompute(df):
    """Every indicator series, and the day-range statistics, computed once.

    Calling compute_technical_signal() per bar would recompute every EMA over
    the whole history each time — O(n^2), which on 20,000 bars is hours. The
    THRESHOLDS below are copied from compute_technical_signal deliberately and
    must be kept identical to it; everything downstream (scoring, gating,
    stops, targets, reachability) then runs through the real functions.
    """
    close = df["Close"]
    out = pd.DataFrame(index=df.index)
    out["ema_fast"] = ind.ema(close, config.EMA_FAST)
    out["ema_slow"] = ind.ema(close, config.EMA_SLOW)
    out["rsi"] = ind.rsi(close, config.RSI_LENGTH)
    out["atr"] = ind.atr(df, config.ATR_LENGTH)
    _, _, hist = ind.macd(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    out["macd_hist"] = hist
    out["adx"] = ind.adx(df, config.ADX_LENGTH)
    out["vwap"] = ind.vwap(df)
    out["swing_low"] = df["Low"].rolling(config.SWING_LOOKBACK, min_periods=1).min()
    out["swing_high"] = df["High"].rolling(config.SWING_LOOKBACK, min_periods=1).max()

    # --- day-range statistics, vectorised ---------------------------------
    day = pd.Series(df.index.date, index=df.index)
    out["day"] = day
    # running high/low WITHIN the day, so "used today" at bar i only knows
    # what had happened by bar i — no peeking at the rest of the session
    out["day_high"] = df.groupby(day)["High"].cummax()
    out["day_low"] = df.groupby(day)["Low"].cummin()
    out["used_today"] = out["day_high"] - out["day_low"]

    full = df.groupby(day).agg(hi=("High", "max"), lo=("Low", "min"))
    full["range"] = full["hi"] - full["lo"]
    # mean of all PRIOR complete days, matching _daily_range_stats, shifted so
    # today's own range is never part of its own average
    prior_mean = full["range"].expanding().mean().shift(1)
    out["typical"] = day.map(prior_mean).astype(float)

    # bar index of the last bar of each day, for square-off
    out["is_day_end"] = day != day.shift(-1)
    return out


def tech_at(df, pre, i):
    """The tech dict for bar i, with exactly the keys compute_technical_signal
    produces and exactly its threshold logic."""
    c = float(df["Close"].iloc[i])
    ef, es = float(pre["ema_fast"].iloc[i]), float(pre["ema_slow"].iloc[i])
    r = float(pre["rsi"].iloc[i])
    h = float(pre["macd_hist"].iloc[i])
    v = float(pre["vwap"].iloc[i])
    a = float(pre["adx"].iloc[i])

    trend = 1 if (c > es and ef > es) else (-1 if (c < es and ef < es) else 0)
    macd_s = 1 if h > 0 else (-1 if h < 0 else 0)
    rsi_s = 1 if (config.RSI_BULL_MIN < r < config.RSI_OVERBOUGHT) else (
        -1 if (config.RSI_OVERSOLD < r < config.RSI_BEAR_MAX) else 0)
    vwap_s = 1 if c > v else (-1 if c < v else 0)

    return {
        "last_close": c, "last_rsi": r, "last_atr": float(pre["atr"].iloc[i]),
        "ema_fast": round(ef, 2), "ema_slow": round(es, 2),
        "trend_score": trend, "macd_score": macd_s, "macd_hist": round(h, 2),
        "rsi_score": rsi_s, "vwap_score": vwap_s,
        "adx": round(a, 1), "adx_ok": bool(a >= config.ADX_TREND_THRESHOLD),
        "vwap": round(v, 2), "vwap_gap": round(c - v, 1),
        "last_swing_low": round(float(pre["swing_low"].iloc[i]), 2),
        "last_swing_high": round(float(pre["swing_high"].iloc[i]), 2),
        "total_score": trend + macd_s + rsi_s + vwap_s, "max_score": 4,
    }


def verify_precompute(df, pre, samples=25):
    """Prove the fast path agrees with the real compute_technical_signal.

    Without this the whole backtest is only testing my copy of the threshold
    logic. Checks a scattering of bars against the genuine function.
    """
    idxs = np.linspace(max(config.EMA_SLOW + 40, 120), len(df) - 1, samples).astype(int)
    bad = []
    for i in idxs:
        real = se.compute_technical_signal(df.iloc[:i + 1])
        fast = tech_at(df, pre, i)
        for k in ("trend_score", "macd_score", "rsi_score", "vwap_score", "adx",
                  "last_close", "vwap", "last_swing_low", "last_swing_high"):
            rv, fv = real[k], fast[k]
            if isinstance(rv, float) and abs(rv - fv) > 0.02:
                bad.append((i, k, rv, fv))
            elif not isinstance(rv, float) and rv != fv:
                bad.append((i, k, rv, fv))
    return bad


# ===========================================================================
# THE WALK
# ===========================================================================
def run(index_key, df, hold_bars=None, square_off=None, min_gap_bars=4, gate=None):
    """hold_bars and square_off default to the instrument's own market.

    26 bars is one Indian session and squaring off at the close is what you do
    to avoid holding an index option overnight. Neither means anything on a
    market that never closes: 24 hours is 96 bars there, and there is no bell
    to flatten into. Left at the index defaults, a crypto backtest would have
    exited every trade after six and a half hours for no reason at all.
    """
    always = config.market_for(index_key)["always_open"]
    if hold_bars is None:
        hold_bars = 96 if always else 26
    if square_off is None:
        square_off = not always
    """Replay history bar by bar through the real engine."""
    pre = precompute(df)
    step = config.INSTRUMENTS[index_key]["strike_step"]
    no_chain = se.compute_option_chain_signal(None)

    warmup = max(config.EMA_SLOW, config.ADX_LENGTH * 2, 60) + 5
    trades, last_i = [], -10 ** 9
    blocked = {"neutral": 0, "adx": 0, "no_room": 0}

    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    closes = df["Close"].to_numpy()
    day_end = pre["is_day_end"].to_numpy()

    for i in range(warmup, len(df) - 1):
        if i - last_i < min_gap_bars:
            continue
        typical = pre["typical"].iloc[i]
        if not np.isfinite(typical):
            continue

        tech = tech_at(df, pre, i)
        if not np.isfinite(tech["last_atr"]) or tech["last_atr"] <= 0:
            continue

        stamp = df.index[i]
        now = stamp.to_pydatetime()
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)

        reach = se.compute_reachability(
            tech["last_close"], no_chain, df, now, adx=tech["adx"],
            range_stats=(float(typical), float(pre["used_today"].iloc[i])),
            index_key=index_key)
        rec = se.build_recommendation(index_key, tech, no_chain, step, reach=reach)

        if rec["bias"] == "NEUTRAL":
            if rec.get("not_worth_it"):
                blocked["no_room"] += 1
            elif rec.get("adx_blocked"):
                blocked["adx"] += 1
            else:
                blocked["neutral"] += 1
            continue

        entry = rec["spot"]
        t1, t2, t3 = rec["index_targets"]
        stop = rec["index_stop_loss"]
        if t1 is None or stop is None:
            continue
        ce = rec["option_type"] == "CE"
        # An optional extra filter, asked at the moment of entry with only what
        # was known then. Applied inside the loop rather than to the finished
        # trade list, so a blocked signal frees the gap for the next one exactly
        # as it would live - filtering afterwards would quietly lose those.
        if gate is not None and not gate(i, rec):
            blocked["regime"] = blocked.get("regime", 0) + 1
            continue

        hit = {"T1": False, "T2": False, "T3": False}
        sl_hit = False
        exit_px, exit_bar, reason = None, None, "open"
        risk = rec["risk_points"] or 1e-9

        # MFE = the furthest the trade ever went in your favour, in R, BEFORE
        # the stop was touched. Recorded independently of where the targets
        # happen to sit, so the accuracy of ANY target distance can be worked
        # out afterwards from one pass — that's what --accuracy-sweep uses.
        mfe_r = 0.0
        final_r = None

        for j in range(i + 1, min(i + 1 + hold_bars, len(df))):
            hi, lo = highs[j], lows[j]
            # Stop checked FIRST within a bar. Both can be touched inside one
            # 15-minute candle and the bar doesn't say which came first, so the
            # pessimistic reading is the only defensible one.
            if (lo <= stop) if ce else (hi >= stop):
                sl_hit = True
                if exit_px is None:
                    exit_px, exit_bar, reason = stop, j, "stop"
                final_r = -1.0
                break
            excursion = ((hi - entry) if ce else (entry - lo)) / risk
            if excursion > mfe_r:
                mfe_r = excursion
            for name, lvl in (("T1", t1), ("T2", t2), ("T3", t3)):
                if not hit[name] and ((hi >= lvl) if ce else (lo <= lvl)):
                    hit[name] = True
            if hit["T3"] and exit_px is None:
                exit_px, exit_bar, reason = t3, j, "t3"
            if square_off and day_end[j]:
                if exit_px is None:
                    exit_px, exit_bar, reason = closes[j], j, "square-off"
                final_r = ((closes[j] - entry) if ce else (entry - closes[j])) / risk
                break
        if exit_px is None:
            last = min(i + hold_bars, len(df) - 1)
            exit_px, exit_bar, reason = closes[last], last, "time"
        if final_r is None:
            last_c = closes[exit_bar if exit_bar is not None else i + 1]
            final_r = ((last_c - entry) if ce else (entry - last_c)) / risk

        r_mult = ((exit_px - entry) if ce else (entry - exit_px)) / risk
        trades.append({
            "when": stamp, "side": rec["option_type"], "entry": entry,
            "t1": t1, "t2": t2, "t3": t3, "stop": stop, "risk": risk,
            "t1_hit": hit["T1"], "t2_hit": hit["T2"], "t3_hit": hit["T3"],
            "sl_hit": sl_hit, "exit": exit_px, "reason": reason,
            "bars_held": exit_bar - i, "r": r_mult,
            "mfe_r": mfe_r, "final_r": final_r,
            "adx": tech["adx"], "sl_basis": rec["sl_basis"],
            "expansion": (reach or {}).get("range_expansion"),
            "reach": rec.get("reach_points"), "rr": rec.get("reach_to_risk"),
        })
        last_i = i

    return {"index": index_key, "trades": trades, "blocked": blocked, "bars": len(df)}


# ===========================================================================
# REPORTING
# ===========================================================================
def summarize(res):
    t = res["trades"]
    n = len(t)
    if not n:
        return {"index": res["index"], "n": 0, "blocked": res["blocked"]}
    rs = [x["r"] for x in t]
    return {
        "index": res["index"], "n": n, "blocked": res["blocked"], "bars": res["bars"],
        "t1": 100 * sum(x["t1_hit"] for x in t) / n,
        "t2": 100 * sum(x["t2_hit"] for x in t) / n,
        "t3": 100 * sum(x["t3_hit"] for x in t) / n,
        "sl": 100 * sum(x["sl_hit"] for x in t) / n,
        "avg_r": sum(rs) / n,
        "total_r": sum(rs),
        "median_r": float(np.median(rs)),
        "win": 100 * sum(1 for x in rs if x > 0) / n,
        "capped": sum(1 for x in t if x["sl_basis"] == "swing_capped"),
        "expanded": sum(1 for x in t if (x["expansion"] or 1) > 1),
        "avg_bars": sum(x["bars_held"] for x in t) / n,
        "avg_risk_pct": 100 * sum(x["risk"] / x["entry"] for x in t) / n,
    }


def print_summary(s, label):
    print("=" * 78)
    print(f" {s['index']}  —  {label}")
    print("=" * 78)
    if not s["n"]:
        b = s["blocked"]
        print(f" No signals at all. Blocked: {b['neutral']} indicator disagreement, "
              f"{b['adx']} weak trend, {b['no_room']} no room.")
        return
    print(f" Signals              : {s['n']}   (~{s['n']/max(1,s['bars']/25/250):.0f} per year)")
    print(f" Reached T1           : {s['t1']:.1f}%")
    print(f" Reached T2           : {s['t2']:.1f}%")
    print(f" Reached T3           : {s['t3']:.1f}%")
    print(f" Stopped out          : {s['sl']:.1f}%")
    print(f" Profitable exits     : {s['win']:.1f}%")
    print(f" Average result       : {s['avg_r']:+.3f} R      (median {s['median_r']:+.3f} R)")
    print(f" Total                : {s['total_r']:+.1f} R over {s['n']} trades")
    print(f" Average stop size    : {s['avg_risk_pct']:.2f}% of spot")
    print(f" Average hold         : {s['avg_bars']:.1f} bars ({s['avg_bars']*15/60:.1f} hours)")
    print(f" Stops capped by MAX_RISK_ATR_MULT : {s['capped']} of {s['n']}")
    print(f" Signals on an expanded-range day  : {s['expanded']} of {s['n']}")
    b = s["blocked"]
    print(f" Rejected before entry: {b['neutral']} disagreement, {b['adx']} weak trend, "
          f"{b['no_room']} no room")
    print()


def accuracy_sweep(all_trades, cost_r=0.15):
    """"How do we get to 70% accuracy?" — answered from your own trades.

    Accuracy is not a property of the signals. It is a property of where you
    put the target. Move the target closer and you hit it more often; you just
    get paid less each time. This sweeps the target distance across a wide
    range and reports, for each one, the hit rate AND what it earns.

    Every row uses the SAME signals, the SAME entries and the SAME stops. The
    only thing that changes is how far away the target sits. If accuracy were
    a real edge, some row would show both a high hit rate and a high return.

    cost_r is what one round trip costs, expressed in R: brokerage, STT,
    exchange charges, the option bid-ask, and theta over the hold. 0.15 is an
    estimate for a NIFTY lot on a ~2.5 hour hold — argue with it by passing
    --cost, but note that setting it to zero is not the honest choice.
    """
    if not all_trades:
        print(" No trades to sweep.")
        return

    print("=" * 78)
    print(" HOW TO GET ANY ACCURACY YOU LIKE — AND WHAT IT PAYS")
    print("=" * 78)
    print(" Same signals, same entries, same stops. Only the target moves.\n")
    print(f" {'target':>8}  {'accuracy':>9}  {'gross R':>9}  {'net R':>9}  "
          f"{'per 1000 trades':>17}")
    print(f" {'(in R)':>8}  {'':>9}  {'/trade':>9}  {'/trade':>9}  {'net R':>17}")
    print(" " + "-" * 72)

    n = len(all_trades)
    best = None
    for k in (0.15, 0.25, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
        wins = 0
        total = 0.0
        for t in all_trades:
            if t["mfe_r"] >= k:
                wins += 1
                total += k                      # target reached, paid k R
            elif t["sl_hit"]:
                total -= 1.0                    # stopped out, lost 1 R
            else:
                total += t["final_r"]           # neither — closed out flat-ish
        acc = 100.0 * wins / n
        gross = total / n
        net = gross - cost_r
        marker = ""
        if acc >= 70 and (best is None or acc < best[0]):
            best = (acc, k, gross, net)
            marker = "  <- 70%+ lands here"
        print(f" {k:>8.2f}  {acc:>8.1f}%  {gross:>+9.3f}  {net:>+9.3f}  "
              f"{net*1000:>+17.0f}{marker}")

    print(" " + "-" * 72)
    if best:
        acc, k, gross, net = best
        print(f"\n There it is: put the target {k:g} R away and you hit it {acc:.0f}% of")
        print(f" the time. It earns {gross:+.3f} R per trade gross, {net:+.3f} R after costs.")
    else:
        print("\n Nothing in this range reaches 70% — the stop is being hit too often")
        print(" for any target, however close, to get there.")
    print()
    print(" This is the whole point. Accuracy is a dial, not an achievement.")
    print(" Turning it up moves money from the size of your wins into the")
    print(" frequency of them, and the total barely moves — except that more")
    print(" frequent trades pay more costs, so the net usually gets WORSE.")
    print(" A strategy is judged by the 'net R' column, never by 'accuracy'.")
    print("=" * 78 + "\n")


def print_verdict(rows, cost_r=0.0):
    print("=" * 78)
    print(" WHAT THIS ACTUALLY MEANS")
    print("=" * 78)
    live = [s for s in rows if s["n"]]
    if not live:
        print(" No trades were generated at all — the settings are too strict to test.")
        print("=" * 78)
        return
    n = sum(s["n"] for s in live)
    total = sum(s["total_r"] for s in live)
    avg = total / n
    print(f" {n} signals across {len(live)} index(es). Average {avg:+.3f} R per trade.\n")
    if avg > 0.10:
        print(" POSITIVE on index points, with room to survive some costs.")
    elif avg > 0.0:
        print(" MARGINALLY POSITIVE on index points. Thin enough that brokerage and")
        print(" the option bid-ask could erase it entirely.")
    else:
        print(" NEGATIVE on index points — before any costs at all.")
        print(" Options only make this worse, because theta charges you for the wait.")
    print()
    print(" Every figure above is in INDEX POINTS, not rupees, and excludes")
    print(" brokerage, STT, slippage and time decay. PCR abstained throughout")
    print(" (no historical option chain exists), so these signals were decided by")
    print(" four indicators, not five.")
    print("=" * 78)


# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="Backtest the real strategy on real 15m candles.")
    ap.add_argument("--years", type=float, default=3)
    ap.add_argument("--index", default=None, help="NIFTY / BANKNIFTY / SENSEX")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--hold-bars", type=int, default=None,
                    help="max bars to hold (default: 26 on an index = one "
                         "session, 96 on crypto = 24h)")
    ap.add_argument("--no-square-off", action="store_true", help="allow holding overnight")
    ap.add_argument("--compare", action="store_true",
                    help="run with the fixes off as well, and print both")
    ap.add_argument("--accuracy-sweep", action="store_true",
                    help="show what target distance gives what accuracy, and what it pays")
    ap.add_argument("--cost", type=float, default=0.15,
                    help="round-trip cost in R (brokerage + spread + theta). Default 0.15")
    args = ap.parse_args()

    keys = [args.index] if args.index else list(config.INSTRUMENTS.keys())

    print(f"\nFetching {args.years:g} years of 15-minute candles from Kite...\n")
    dfs = {}
    for k in keys:
        try:
            dfs[k] = fetch_history(k, args.years, use_cache=not args.no_cache)
        except Exception as exc:
            print(f"  {k}: FAILED — {exc}")
    if not dfs:
        print("\nNothing to test. A valid Kite login is required — run kite_auth.py.")
        return

    print("\nVerifying the fast indicator path against the real engine...")
    for k, df in dfs.items():
        bad = verify_precompute(df, precompute(df))
        if bad:
            print(f"  {k}: MISMATCH — the backtest does not agree with the live code:")
            for b in bad[:5]:
                print(f"     bar {b[0]} {b[1]}: real {b[2]} vs backtest {b[3]}")
            print("  Refusing to report numbers from a strategy that isn't the real one.")
            return
        print(f"  {k}: matches compute_technical_signal exactly")
    print()

    pooled = []

    def pass_(label, collect=False):
        rows = []
        for k, df in dfs.items():
            res = run(k, df, hold_bars=args.hold_bars,
                      square_off=(False if args.no_square_off else None))
            s = summarize(res)
            print_summary(s, label)
            rows.append(s)
            if collect:
                pooled.extend(res["trades"])
        return rows

    if args.accuracy_sweep:
        pass_("current settings", collect=True)
        accuracy_sweep(pooled, cost_r=args.cost)
        print_verdict([summarize({"index": "all", "trades": pooled,
                                  "blocked": {"neutral": 0, "adx": 0, "no_room": 0},
                                  "bars": 0})])
        return

    if args.compare:
        keep_exp, keep_cap = config.TREND_RANGE_EXPANSION, config.MAX_RISK_ATR_MULT
        config.TREND_RANGE_EXPANSION, config.MAX_RISK_ATR_MULT = False, 9999.0
        print("\n" + "#" * 78)
        print("# BEFORE the fixes (no trend expansion, no stop cap)")
        print("#" * 78 + "\n")
        before = pass_("BEFORE")
        config.TREND_RANGE_EXPANSION, config.MAX_RISK_ATR_MULT = keep_exp, keep_cap
        print("\n" + "#" * 78)
        print("# AFTER the fixes")
        print("#" * 78 + "\n")
        after = pass_("AFTER")

        print("=" * 78)
        print(" SIDE BY SIDE")
        print("=" * 78)
        print(f" {'index':<12}{'signals':>16}{'T2 hit':>16}{'avg R':>16}")
        for b, a in zip(before, after):
            bs = f"{b['n']} -> {a['n']}"
            bt = (f"{b['t2']:.0f}% -> {a['t2']:.0f}%" if b["n"] and a["n"] else "n/a")
            br = (f"{b['avg_r']:+.3f} -> {a['avg_r']:+.3f}" if b["n"] and a["n"] else "n/a")
            print(f" {b['index']:<12}{bs:>16}{bt:>16}{br:>16}")
        print("=" * 78)
        print(" If avg R went DOWN, the fixes bought more trades at worse odds and")
        print(" should be reverted: TREND_RANGE_EXPANSION = False and a high")
        print(" MAX_RISK_ATR_MULT in config.py put it back exactly.")
        print("=" * 78 + "\n")
        print_verdict(after)
    else:
        print_verdict(pass_("current settings"))

    print("\nCopy this whole output and paste it back if you'd like it read for you.")
    print("Reminder: index points, not rupees. Not investment advice.\n")


if __name__ == "__main__":
    main()
