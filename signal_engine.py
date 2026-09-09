"""
signal_engine.py
------------------
Combines technical indicators + (optional) option-chain OI data into a
single bias score and a concrete, actionable suggestion: which side (CE/PE),
which strike, entry zone, target, and stop-loss.

IMPORTANT: this is a rule-based heuristic, not a prediction. See README for
the full disclaimer. Nothing here should be treated as guaranteed-accurate.
"""

import math
import datetime as dt

import pandas as pd

import config
import indicators as ind

# NSE/BSE session, used to work out how much trading time is left today.
# Read from config rather than repeated here: this copy said 15:30 for five
# weeks after NSE moved the derivatives close to 15:40, which quietly shortened
# every reachability estimate by ten minutes.
MARKET_OPEN_H, MARKET_OPEN_M = config.MARKET_OPEN_TIME
MARKET_CLOSE_H, MARKET_CLOSE_M = config.MARKET_CLOSE_TIME
SESSION_HOURS = config.session_hours()


def round_to_step(value: float, step: int) -> int:
    return int(round(value / step) * step)


def compute_technical_signal(df: pd.DataFrame) -> dict:
    close = df["Close"]
    ema_fast = ind.ema(close, config.EMA_FAST)
    ema_slow = ind.ema(close, config.EMA_SLOW)
    rsi = ind.rsi(close, config.RSI_LENGTH)
    atr = ind.atr(df, config.ATR_LENGTH)
    macd_line, macd_signal, macd_hist = ind.macd(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    adx = ind.adx(df, config.ADX_LENGTH)
    vwap = ind.vwap(df)

    last_close = close.iloc[-1]
    last_rsi = rsi.iloc[-1]
    last_atr = atr.iloc[-1]
    last_macd_hist = macd_hist.iloc[-1]
    last_adx = adx.iloc[-1]
    last_vwap = vwap.iloc[-1]
    trend_up = last_close > ema_slow.iloc[-1] and ema_fast.iloc[-1] > ema_slow.iloc[-1]
    trend_down = last_close < ema_slow.iloc[-1] and ema_fast.iloc[-1] < ema_slow.iloc[-1]

    trend_score = 1 if trend_up else (-1 if trend_down else 0)
    # MACD histogram — momentum accelerating up/down, not just "above/below
    # a line" like trend/VWAP are, so this is a genuinely different vote.
    macd_score = 1 if last_macd_hist > 0 else (-1 if last_macd_hist < 0 else 0)
    rsi_score = 1 if (config.RSI_BULL_MIN < last_rsi < config.RSI_OVERBOUGHT) else (
        -1 if (config.RSI_OVERSOLD < last_rsi < config.RSI_BEAR_MAX) else 0
    )
    vwap_score = 1 if last_close > last_vwap else (-1 if last_close < last_vwap else 0)

    total = trend_score + macd_score + rsi_score + vwap_score

    # Recent swing high/low — a simple, robust proxy for "the nearest real
    # support/resistance," used to anchor the stop-loss structurally
    # instead of a fixed ATR distance with no relationship to the chart.
    lookback = min(config.SWING_LOOKBACK, len(df))
    last_swing_low = float(df["Low"].iloc[-lookback:].min())
    last_swing_high = float(df["High"].iloc[-lookback:].max())

    return {
        "last_close": last_close,
        "last_rsi": last_rsi,
        "last_atr": last_atr,
        # Exposed so the explanation can quote the actual levels the trend
        # vote was decided on, instead of just asserting "uptrend".
        "ema_fast": round(float(ema_fast.iloc[-1]), 2),
        "ema_slow": round(float(ema_slow.iloc[-1]), 2),
        "trend_score": trend_score,
        "macd_score": macd_score,
        "macd_hist": round(float(last_macd_hist), 2),
        "rsi_score": rsi_score,
        "vwap_score": vwap_score,
        "adx": round(float(last_adx), 1),
        "adx_ok": bool(last_adx >= config.ADX_TREND_THRESHOLD),
        "vwap": round(float(last_vwap), 2),
        "vwap_gap": round(float(last_close - last_vwap), 1),   # + = above VWAP
        "last_swing_low": round(last_swing_low, 2),
        "last_swing_high": round(last_swing_high, 2),
        "total_score": total,          # range -4..+4
        "max_score": 4,
    }


def _resample_htf(df: pd.DataFrame, rule: str = "1h") -> pd.DataFrame:
    """Roll the fetched candles up into a higher timeframe (default 1 hour)
    so we can read the bigger-picture trend from the same data we already
    fetched — no extra network call needed."""
    try:
        out = df.resample(rule).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        )
        return out.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def _ema_direction(df: pd.DataFrame) -> str:
    """UP / DOWN / FLAT from price vs EMA20/EMA50 — the same trend test the
    scoring engine uses, factored out so it can be reused per timeframe."""
    if len(df) < config.EMA_SLOW:
        return "FLAT"
    close = df["Close"]
    ema_fast = ind.ema(close, config.EMA_FAST).iloc[-1]
    ema_slow = ind.ema(close, config.EMA_SLOW).iloc[-1]
    last = close.iloc[-1]
    if last > ema_slow and ema_fast > ema_slow:
        return "UP"
    if last < ema_slow and ema_fast < ema_slow:
        return "DOWN"
    return "FLAT"


def compute_market_trend(df: pd.DataFrame) -> dict:
    """Reads the overall market condition — direction, strength, momentum,
    and where price sits in today's range.

    This is deliberately SEPARATE from the CE/PE signal. The signal only
    fires when nearly every indicator agrees, so most of the time it says
    "WAIT" and tells you nothing about what the market is actually doing.
    This answers the simpler, always-relevant question: is it trending up,
    trending down, or just chopping sideways — and how strongly?
    """
    close = df["Close"]
    last = float(close.iloc[-1])

    adx_series = ind.adx(df, config.ADX_LENGTH)
    adx_val = float(adx_series.iloc[-1])
    _, _, macd_hist = ind.macd(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    vwap_series = ind.vwap(df)
    vwap_last = float(vwap_series.iloc[-1])

    direction = _ema_direction(df)

    # How far has price NET travelled over the ADX window, measured in ATR so
    # the number means the same thing on Nifty as on BankNifty? A trend that
    # hasn't moved anywhere isn't a trend, whatever ADX reads. See config.py.
    displacement = None
    try:
        n = min(config.ADX_LENGTH, len(close) - 1)
        atr_now = float(ind.atr(df, config.ATR_LENGTH).iloc[-1])
        if n > 0 and atr_now > 0:
            displacement = abs(last - float(close.iloc[-1 - n])) / atr_now
    except Exception:
        displacement = None

    # ADX needs a decent run of bars before it means anything — on a handful
    # of candles it can read 100 purely as an artifact. Say so rather than
    # reporting a confident "STRONG TREND" built on nothing.
    enough_data = len(df) >= max(config.ADX_LENGTH * 2, config.EMA_SLOW)

    # ADX measures trend STRENGTH only (never direction). Below the gate
    # threshold the market is chop, whatever the moving averages imply.
    if not enough_data:
        strength = "UNKNOWN"
    elif adx_val >= 25:
        strength = "STRONG"
    elif adx_val >= config.ADX_TREND_THRESHOLD:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    # Now hold that reading against reality. ADX can stay high for hours after
    # a move has finished; price cannot lie about where it is.
    stalled = False
    if displacement is not None and strength in ("STRONG", "MODERATE"):
        if displacement < config.TREND_MIN_DISPLACEMENT_ATR:
            strength, stalled = "WEAK", True
        elif (strength == "STRONG"
              and displacement < config.TREND_STRONG_DISPLACEMENT_ATR):
            strength = "MODERATE"

    if strength == "UNKNOWN":
        label = "NOT ENOUGH DATA YET"
    elif stalled:
        label = "STALLED — GOING NOWHERE"
    elif strength == "WEAK":
        label = "RANGE-BOUND / CHOPPY"
    elif direction == "UP":
        label = f"{strength} UPTREND"
    elif direction == "DOWN":
        label = f"{strength} DOWNTREND"
    else:
        label = "NO CLEAR TREND"

    # Momentum: is the MACD histogram building or fading? Compared a few
    # bars back rather than to the previous bar, which is too twitchy.
    hist_now = float(macd_hist.iloc[-1])
    lookback = min(3, len(macd_hist) - 1)
    hist_prev = float(macd_hist.iloc[-1 - lookback]) if lookback > 0 else hist_now
    if abs(hist_now) > abs(hist_prev):
        momentum = "building"
    elif abs(hist_now) < abs(hist_prev):
        momentum = "fading"
    else:
        momentum = "flat"

    # ---- today's session stats -------------------------------------------
    day_open = day_high = day_low = None
    day_change = day_change_pct = range_pos = None
    try:
        last_day = df.index[-1].date()
        day_df = df[[d == last_day for d in df.index.date]]
        if not day_df.empty:
            day_open = float(day_df["Open"].iloc[0])
            day_high = float(day_df["High"].max())
            day_low = float(day_df["Low"].min())
            day_change = round(last - day_open, 2)
            if day_open:
                day_change_pct = round(day_change / day_open * 100, 2)
            span = day_high - day_low
            if span > 0:
                range_pos = round((last - day_low) / span * 100, 1)
    except Exception:
        pass

    # Higher-timeframe read only makes sense if the candles we have are
    # SHORTER than an hour — rolling daily bars up into "1 hour" would just
    # be the same bars relabelled, which would be misleading rather than
    # informative. In that case report it as unavailable.
    htf_direction = None
    try:
        if len(df.index) > 2:
            spacing = pd.Series(df.index).diff().dropna().median()
            if spacing is not None and spacing < pd.Timedelta("1h"):
                htf = _resample_htf(df, "1h")
                if not htf.empty:
                    htf_direction = _ema_direction(htf)
    except Exception:
        htf_direction = None

    return {
        "direction": direction,               # UP / DOWN / FLAT (this timeframe)
        "strength": strength,                 # STRONG / MODERATE / WEAK
        "label": label,                       # human-readable combined verdict
        "adx": round(adx_val, 1),
        "displacement_atr": round(displacement, 2) if displacement is not None else None,
        "stalled": stalled,
        "momentum": momentum,                 # building / fading / flat
        "vs_vwap": "ABOVE" if last > vwap_last else ("BELOW" if last < vwap_last else "AT"),
        "vwap": round(vwap_last, 2),
        "htf_direction": htf_direction,       # 1-hour trend (None if not derivable)
        "last": round(last, 2),
        "day_open": round(day_open, 2) if day_open is not None else None,
        "day_high": round(day_high, 2) if day_high is not None else None,
        "day_low": round(day_low, 2) if day_low is not None else None,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
        "range_pos_pct": range_pos,           # 0 = at day's low, 100 = at day's high
    }


def compute_option_chain_signal(chain: dict) -> dict:
    if chain is None or chain.get("pcr") is None:
        return {"available": False, "oi_score": 0, "notes": "Option chain data unavailable."}

    pcr = chain["pcr"]
    # PCR > 1.2 => heavier put writing => often bullish (support building)
    # PCR < 0.8 => heavier call writing => often bearish (resistance building)
    if pcr > 1.2:
        oi_score = 1
    elif pcr < 0.8:
        oi_score = -1
    else:
        oi_score = 0

    notes = (
        f"PCR={pcr} | Max Pain={chain['max_pain']} | "
        f"Resistance (top Call OI)={chain['top_call_oi_strike']} | "
        f"Support (top Put OI)={chain['top_put_oi_strike']}"
    )
    return {"available": True, "oi_score": oi_score, "notes": notes, **chain}


def _parse_expiry(value) -> "dt.date":
    """Expiry comes through in different shapes depending on the source —
    NSE gives '14-Aug-2026', Kite gives a date/datetime or ISO string."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _trading_hours_until(expiry_date, now: dt.datetime) -> tuple:
    """(hours left in TODAY's session, total trading hours until expiry).

    Used to scale the option market's expected move — which is quoted for
    the whole period up to expiry — down to just the time actually left.
    Weekends are skipped; exchange holidays are not (a small overestimate,
    which errs toward being conservative about reachability)."""
    open_t = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)

    if now <= open_t:
        hours_left_today = SESSION_HOURS
    elif now >= close_t:
        hours_left_today = 0.0
    else:
        hours_left_today = (close_t - now).total_seconds() / 3600.0

    if expiry_date is None:
        return hours_left_today, max(hours_left_today, SESSION_HOURS)

    # Whole trading days strictly AFTER today, up to and including expiry day.
    extra_days = 0
    d = now.date() + dt.timedelta(days=1)
    while d <= expiry_date and extra_days < 60:
        if d.weekday() < 5:
            extra_days += 1
        d += dt.timedelta(days=1)

    total = hours_left_today + extra_days * SESSION_HOURS
    return hours_left_today, max(total, 0.25)


def _atm_straddle(oi: dict, spot: float):
    """Call price + put price at the strike nearest spot. This is the option
    market's own estimate of how far the index moves by expiry — real money
    from real traders, not a formula we invented."""
    if not oi.get("available") or not oi.get("strikes"):
        return None, None
    usable = [s for s in oi["strikes"]
              if s.get("call_ltp") is not None and s.get("put_ltp") is not None]
    if not usable:
        return None, None
    atm = min(usable, key=lambda s: abs(s["strike"] - spot))
    straddle = float(atm["call_ltp"]) + float(atm["put_ltp"])
    return atm["strike"], straddle


def _daily_range_stats(df: pd.DataFrame) -> tuple:
    """(typical full-day high-low range, how much of it today has used).

    Measured from the candles we already have rather than assumed, so it
    adapts to whatever the index's current volatility actually is."""
    try:
        dates = list(df.index.date)
        by_day = {}
        for d, hi, lo in zip(dates, df["High"], df["Low"]):
            if pd.isna(hi) or pd.isna(lo):
                continue
            if d not in by_day:
                by_day[d] = [hi, lo]
            else:
                by_day[d][0] = max(by_day[d][0], hi)
                by_day[d][1] = min(by_day[d][1], lo)
        if not by_day:
            return None, None
        today = dates[-1]
        prior = [hi - lo for d, (hi, lo) in by_day.items() if d != today]
        used_today = by_day[today][0] - by_day[today][1]
        typical = (sum(prior) / len(prior)) if prior else None
        return (float(typical) if typical is not None else None), float(used_today)
    except Exception:
        return None, None


def compute_reachability(spot: float, oi: dict, df: pd.DataFrame, now: dt.datetime,
                          adx: float = None, range_stats: tuple = None) -> dict:
    """How far can this index REALISTICALLY travel from here, in each
    direction, before the session ends?

    Three independent limits, all measured from live market data:

      1. Expected move — the ATM straddle price is what the option market
         is collectively pricing as the move by expiry. Scaled by the square
         root of remaining time to give the move left for today.
      2. Open-interest walls — the strike with the heaviest call OI tends to
         cap rallies; heaviest put OI tends to floor declines.
      3. Today's remaining range — if the index normally travels ~250 points
         a day and has already covered 200, roughly 50 are left.

    The tightest of the three wins. That distance is what targets get built
    from, instead of an arbitrary multiple of the stop distance.
    """
    out = {
        "available": False, "expected_move_expiry": None, "expected_move_remaining": None,
        "hours_left_today": None, "resistance": None, "support": None,
        "typical_daily_range": None, "used_today": None, "room_left_today": None,
        "reach_up": None, "reach_down": None, "cap_up": None, "cap_down": None,
        "atm_strike": None, "expiry": None,
    }

    expiry_date = _parse_expiry(oi.get("expiry")) if oi else None
    hours_left, hours_to_expiry = _trading_hours_until(expiry_date, now)
    out["hours_left_today"] = round(hours_left, 2)
    out["expiry"] = str(oi.get("expiry")) if oi else None

    # ---- 1. expected move from the option market ------------------------
    atm_strike, straddle = _atm_straddle(oi or {}, spot)
    em_remaining = None
    if straddle and straddle > 0:
        out["atm_strike"] = atm_strike
        out["expected_move_expiry"] = round(straddle, 2)
        # Volatility scales with the square root of time, so the move left
        # for today is the full-period move times sqrt(time remaining).
        frac = max(0.0, min(1.0, hours_left / hours_to_expiry)) if hours_to_expiry else 0.0
        em_remaining = straddle * math.sqrt(frac)
        out["expected_move_remaining"] = round(em_remaining, 2)

    # ---- 2. open-interest walls -----------------------------------------
    res = oi.get("top_call_oi_strike") if oi else None
    sup = oi.get("top_put_oi_strike") if oi else None
    out["resistance"] = res if (res is not None and res > spot) else None
    out["support"] = sup if (sup is not None and sup < spot) else None

    # ---- 3. how much of a normal day is left ----------------------------
    # range_stats lets a caller hand in (typical, used) it has already worked
    # out. _daily_range_stats walks the whole frame, which is fine once a
    # second live but O(n^2) when a backtest replays 20,000 bars — the
    # intraday backtest precomputes them vectorised and passes them in, so it
    # exercises this exact function rather than a reimplementation of it.
    typical, used = range_stats if range_stats is not None else _daily_range_stats(df)

    # A trend day IS a range-expansion day, so "a normal day's range" is the
    # wrong yardstick when the trend is strong — see the note in config.py.
    # Scaling by trend strength stops this limit from being tightest exactly
    # when the market is most likely to keep going.
    if adx is None:
        try:
            adx = float(ind.adx(df, config.ADX_LENGTH).iloc[-1])
        except Exception:
            adx = None
    expansion = config.range_expansion(adx)
    out["range_expansion"] = expansion
    out["adx_used"] = round(adx, 1) if adx is not None else None
    if typical is not None:
        typical = typical * expansion

    out["typical_daily_range"] = round(typical, 2) if typical else None
    out["used_today"] = round(used, 2) if used is not None else None
    room_left = None
    if typical is not None and used is not None:
        room_left = max(0.0, typical - used)
        out["room_left_today"] = round(room_left, 2)

    # A trending day routinely BLOWS THROUGH its typical range — that is what
    # a trend is. So "the day has used its usual range" must not be treated as
    # a hard ceiling, or the tool goes blindest exactly on the days worth
    # trading. It also used to produce an absurd cliff: at 93% of the range
    # used the trade was rejected, at 100% used the limit hit zero, got
    # filtered out as non-positive, and the trade was fine again.
    #
    # It is now floored so it can never bind below half the expected move,
    # which keeps it as a sensible drag without letting it veto anything.
    if room_left is not None and em_remaining:
        room_left = max(room_left, 0.5 * em_remaining)

    def tightest(limits):
        """limits: list of (distance, reason). Smallest positive wins."""
        valid = [(d, r) for d, r in limits if d is not None and d > 0]
        if not valid:
            return None, None
        d, r = min(valid, key=lambda x: x[0])
        return round(d, 2), r

    # OI walls are resistance, not a brick wall — price trades through them
    # regularly, especially on news. Capping T3 at the wall is sensible; but
    # a wall sitting close to spot must not shrink the reach so far that the
    # whole trade gets rejected, so it too is floored against the expected
    # move rather than allowed to dominate.
    res_dist = (out["resistance"] - spot) if out["resistance"] else None
    sup_dist = (spot - out["support"]) if out["support"] else None
    if em_remaining:
        if res_dist is not None:
            res_dist = max(res_dist, 0.5 * em_remaining)
        if sup_dist is not None:
            sup_dist = max(sup_dist, 0.5 * em_remaining)

    up_limits = [
        (em_remaining, "the option market's expected move for the time left"),
        (res_dist, f"the call-OI wall at {out['resistance']}"),
        (room_left, "how much of a normal day's range is left"),
    ]
    down_limits = [
        (em_remaining, "the option market's expected move for the time left"),
        (sup_dist, f"the put-OI wall at {out['support']}"),
        (room_left, "how much of a normal day's range is left"),
    ]

    out["reach_up"], out["cap_up"] = tightest(up_limits)
    out["reach_down"], out["cap_down"] = tightest(down_limits)
    out["available"] = out["reach_up"] is not None or out["reach_down"] is not None
    return out


def _find_strike_ltp(oi: dict, strike: int, option_type: str):
    """Look up the real live LTP for a given strike/option_type from the
    fetched option chain data, if available. Returns None if not found."""
    if not oi.get("available") or not oi.get("strikes"):
        return None
    key = "call_ltp" if option_type == "CE" else "put_ltp"
    # exact match first
    for s in oi["strikes"]:
        if s["strike"] == strike:
            return s.get(key)
    # fallback: nearest strike present in the chain
    nearest = min(oi["strikes"], key=lambda s: abs(s["strike"] - strike))
    return nearest.get(key)


def build_recommendation(index_key: str, tech: dict, oi: dict, strike_step: int,
                          reach: dict = None) -> dict:
    total = tech["total_score"] + oi["oi_score"]
    max_total = tech["max_score"] + (1 if oi["available"] else 0)

    votes = {
        "Trend": tech.get("trend_score", 0),
        "MACD": tech.get("macd_score", 0),
        "RSI": tech.get("rsi_score", 0),
        "VWAP": tech.get("vwap_score", 0),
    }
    if oi["available"]:
        votes["PCR"] = oi["oi_score"]

    # An indicator that ABSTAINS must not raise the bar.
    #
    # This used to be a real bug: the threshold was derived from how many
    # indicators EXIST, not how many actually had an opinion. So whenever
    # PCR sat in its neutral 0.8-1.2 band (which is most of the time), it
    # contributed nothing to the score yet still pushed the requirement from
    # 3 up to 4 — meaning three agreeing technicals fired a signal WITHOUT
    # option-chain data but stayed silent WITH it. Having more information
    # made the tool less willing to trade, which is exactly backwards.
    #
    # Now only indicators that actually voted count toward the requirement.
    # Votes AGAINST still count in full — abstaining is neutral, disagreeing
    # is not. The floor of 3 stops a lone voter from being able to fire.
    voting = sum(1 for v in votes.values() if v != 0)
    abstained = [k for k, v in votes.items() if v == 0]

    strict = config.strictness()
    adx_needed = strict["adx"]
    min_agree = strict["min_agree"]
    max_dissent = strict["max_dissent"]

    # Decide by COUNTING agreement, not by a score threshold — see the note
    # in config.py on why a threshold silently demanded unanimity.
    bulls = sum(1 for v in votes.values() if v > 0)
    bears = sum(1 for v in votes.values() if v < 0)
    if bulls > bears:
        lean_dir, agree, dissent = "BULLISH", bulls, bears
    elif bears > bulls:
        lean_dir, agree, dissent = "BEARISH", bears, bulls
    else:
        lean_dir, agree, dissent = None, bulls, bears

    if lean_dir and agree >= min_agree and dissent <= max_dissent:
        raw_bias = lean_dir
    else:
        raw_bias = "NEUTRAL"

    # Kept for display and for anything reading the old field.
    threshold = min_agree

    # ADX gate: even near-unanimous indicator agreement can just be noise
    # in a low-ADX (choppy/directionless) market, so a weak-trend reading
    # vetoes an otherwise-qualifying signal rather than voting on direction.
    adx_val = tech.get("adx")
    adx_ok = adx_val is None or adx_val >= adx_needed
    adx_blocked = raw_bias != "NEUTRAL" and not adx_ok

    # Track exactly what is standing between "now" and a trade, so the tool
    # can explain its silence instead of just showing nothing.
    blockers = []
    if raw_bias == "NEUTRAL":
        lean = lean_dir.lower() if lean_dir else "neither way"
        against = [k for k, v in votes.items()
                   if (v < 0 if lean_dir == "BULLISH" else v > 0)] if lean_dir else []
        if not lean_dir:
            detail = f"{bulls} bullish vs {bears} bearish — dead even"
        elif agree < min_agree:
            detail = (f"only {agree} agree, need at least {min_agree} "
                      f"({voting} of {max_total} indicators voted)")
        else:
            detail = (f"{agree} agree but {dissent} disagree, and only "
                      f"{max_dissent} may disagree")
        if against:
            detail += f"; against: {', '.join(against)}"
        if abstained:
            detail += f"; undecided (not counted against you): {', '.join(abstained)}"
        blockers.append(f"Indicators not agreeing enough (leaning {lean}). {detail}.")
    if adx_blocked:
        blockers.append(
            f"Trend too weak — ADX {adx_val} is below {adx_needed}. The market is "
            f"chopping, so agreement here is more likely noise than a real move."
        )

    if adx_blocked:
        bias = "NEUTRAL"
        action = (f"NO CLEAR TRADE - WAIT (indicators agree on {raw_bias.lower()}, but ADX="
                  f"{tech.get('adx')} < {config.ADX_TREND_THRESHOLD} — trend too weak, high chop risk)")
    elif raw_bias == "BULLISH":
        bias = "BULLISH"
        action = "BUY CE (Call)"
    elif raw_bias == "BEARISH":
        bias = "BEARISH"
        action = "BUY PE (Put)"
    else:
        bias = "NEUTRAL"
        action = "NO CLEAR TRADE - WAIT"

    spot = tech["last_close"]
    atm_strike = round_to_step(spot, strike_step)
    atr = tech["last_atr"]

    index_targets = [None, None, None]
    index_sl = None
    suggested_strike = atm_strike
    option_type = None
    sl_basis = None
    risk_points = None
    target_basis = None

    min_risk = atr * config.MIN_RISK_ATR_MULT
    # A swing that is a very long way off is not a usable intraday stop, and
    # because reward:risk divides by this distance, an over-wide stop can veto
    # a good trade all by itself. See the note in config.py.
    max_risk = atr * config.MAX_RISK_ATR_MULT

    if bias == "BULLISH":
        option_type = "CE"
        swing_low = tech.get("last_swing_low")
        if swing_low is not None and swing_low < spot and (spot - swing_low) >= min_risk:
            index_sl = round(swing_low - atr * config.SL_BUFFER_ATR_MULT, 2)
            sl_basis = "swing"
        else:
            index_sl = round(spot - atr * config.SL_ATR_MULT, 2)
            sl_basis = "atr_fallback"
        risk_points = round(spot - index_sl, 2)
        if risk_points > max_risk:
            index_sl = round(spot - max_risk, 2)
            risk_points = round(spot - index_sl, 2)
            sl_basis = "swing_capped"
        if risk_points <= 0:  # safety net — shouldn't normally trigger
            index_sl = round(spot - atr * config.SL_ATR_MULT, 2)
            risk_points = round(spot - index_sl, 2)
            sl_basis = "atr_fallback"
        index_targets = [round(spot + risk_points * m, 2) for m in config.RR_MULTS]
        target_basis = "risk_multiple"
    elif bias == "BEARISH":
        option_type = "PE"
        swing_high = tech.get("last_swing_high")
        if swing_high is not None and swing_high > spot and (swing_high - spot) >= min_risk:
            index_sl = round(swing_high + atr * config.SL_BUFFER_ATR_MULT, 2)
            sl_basis = "swing"
        else:
            index_sl = round(spot + atr * config.SL_ATR_MULT, 2)
            sl_basis = "atr_fallback"
        risk_points = round(index_sl - spot, 2)
        if risk_points > max_risk:
            index_sl = round(spot + max_risk, 2)
            risk_points = round(index_sl - spot, 2)
            sl_basis = "swing_capped"
        if risk_points <= 0:  # safety net — shouldn't normally trigger
            index_sl = round(spot + atr * config.SL_ATR_MULT, 2)
            risk_points = round(index_sl - spot, 2)
            sl_basis = "atr_fallback"
        index_targets = [round(spot - risk_points * m, 2) for m in config.RR_MULTS]
        target_basis = "risk_multiple"

    # ---------------------------------------------------------------------
    # MARKET-BASED TARGETS
    # ---------------------------------------------------------------------
    # The risk-multiple targets above are just "N times what I'm risking" —
    # nothing about them says the market can actually get there. If we know
    # how far it can realistically travel (from the option market's expected
    # move, the OI walls, and the day's remaining range), place the targets
    # inside THAT distance instead, and refuse the trade outright when
    # there isn't enough room to be worth the risk.
    reach_used = reach_reason = reach_to_risk = None
    not_worth_it = False
    blocked_reason = None

    have_reach = bool(reach and reach.get("available"))
    if config.TARGET_MODE == "market" and option_type and not have_reach \
            and getattr(config, "REQUIRE_REACHABILITY", True):
        # No chain, so no way to ask "can the market even get there?". The old
        # code fell through to risk-multiple targets and issued the ticket
        # regardless — which is how trades got taken with no room check at all.
        # Refusing is the honest answer: the setup may well be real, but the
        # question that decides whether it is WORTH taking cannot be answered.
        not_worth_it = True
        blocked_reason = "no_chain"
        index_targets = [None, None, None]
        target_basis = "no_reach_data"
        bias_before = bias
        bias = "NEUTRAL"
        action = (
            f"NO TRADE - the {bias_before.lower()} setup is real, but option chain data is "
            f"unavailable, so there is no way to check whether the target is reachable "
            f"before the stop is. Waiting for the chain rather than guessing."
        )
        blockers.append(
            "No option chain right now — the room-to-run check cannot run, so no "
            "ticket is issued. This usually clears on its own within a cycle or two."
        )
    elif config.TARGET_MODE == "market" and option_type and have_reach:
        r = reach["reach_up"] if option_type == "CE" else reach["reach_down"]
        why = reach["cap_up"] if option_type == "CE" else reach["cap_down"]
        if r is not None and r > 0:
            reach_used, reach_reason = r, why
            reach_to_risk = round(r / risk_points, 2) if risk_points else None

            if reach_to_risk is not None and reach_to_risk < strict["min_rr"]:
                # The market would have to hand back less than you're risking
                # — no target is worth printing here.
                not_worth_it = True
                blocked_reason = "no_room"
                index_targets = [None, None, None]
                target_basis = "not_reachable"
                bias_before = bias
                bias = "NEUTRAL"
                action = (
                    f"NO TRADE - the {bias_before.lower()} setup is real, but the market can only "
                    f"realistically travel {r} pts from here (limited by {why}), while the stop is "
                    f"{risk_points} pts away. Risking more than the market is likely to give back."
                )
                blockers.append(
                    f"Not enough room to profit — only {r} pts reachable ({why}) against a "
                    f"{risk_points} pt stop, a reward:risk of {reach_to_risk}:1."
                )
            else:
                sign = 1 if option_type == "CE" else -1
                index_targets = [round(spot + sign * r * f, 2) for f in config.REACH_FRACTIONS]
                target_basis = "market_reach"

    # ---- Real option premium (LTP) for the suggested strike, if we have chain data ----
    live_ltp = _find_strike_ltp(oi, suggested_strike, option_type) if option_type else None

    premium_targets = [None, None, None]
    premium_sl = None
    premium_source = None

    if live_ltp and target_basis == "market_reach" and index_targets[0] is not None:
        # Market-based mode: convert each index-level target into the premium
        # it implies, using ~0.5 delta for an at-the-money option. Grounded in
        # the actual distance the index has to travel, rather than a flat
        # "+20% / +40% / +75%" that ignores whether such a move is possible.
        # (Delta is an approximation and ignores time decay, which eats into
        # the premium the longer the move takes — so treat these as a guide.)
        premium_targets = [
            round(live_ltp + abs(t - spot) * config.APPROX_ATM_DELTA, 2) for t in index_targets
        ]
        premium_sl = round(
            max(0.05, live_ltp - abs(index_sl - spot) * config.APPROX_ATM_DELTA), 2
        )
        premium_source = "live"
    elif live_ltp and target_basis != "not_reachable":
        premium_targets = [round(live_ltp * (1 + pct / 100), 2) for pct in config.PREMIUM_TARGET_PCTS]
        premium_sl = round(live_ltp * (1 - config.PREMIUM_SL_PCT / 100), 2)
        premium_source = "live"
    elif option_type and index_targets[0] is not None:
        # Fallback: no live chain LTP available (e.g. Sensex in free mode) —
        # rough delta-based estimate of premium MOVE (not an absolute price,
        # since we don't know the real premium to base a % off of).
        premium_targets = [
            round(abs(t - spot) * config.APPROX_ATM_DELTA, 1) for t in index_targets
        ]
        premium_sl = round(abs(index_sl - spot) * config.APPROX_ATM_DELTA, 1)
        premium_source = "approx_move"

    # Confidence from the vote split rather than the raw score, so it lines
    # up with how the decision is actually made.
    if dissent == 0 and agree >= 4:
        confidence = "High"
    elif dissent == 0 or agree >= 4:
        confidence = "Medium"
    else:
        confidence = "Low"
    if bias == "NEUTRAL":
        confidence = "N/A"

    return {
        "index": index_key,
        "bias": bias,
        "action": action,
        "confidence": confidence,
        "spot": round(spot, 2),
        "atm_strike": atm_strike,
        "suggested_strike": suggested_strike,
        "option_type": option_type,
        "index_targets": index_targets,          # [T1, T2, T3]
        "index_stop_loss": index_sl,
        "live_ltp": live_ltp,                     # real premium, if chain data was available
        "premium_targets": premium_targets,        # [T1, T2, T3] — real $ price if live_ltp set, else approx point-move
        "premium_stop_loss": premium_sl,
        "premium_source": premium_source,          # "live" | "approx_move" | None
        "sl_basis": sl_basis,                      # "swing" | "atr_fallback" | None
        "risk_points": risk_points,                 # entry-to-stop distance (1R), in index points
        "raw_bias": raw_bias,                       # what the score alone said, before the ADX gate
        "adx_blocked": adx_blocked,                  # True if a real signal was vetoed for weak trend strength
        "target_basis": target_basis,               # "market_reach" | "risk_multiple" | "not_reachable"
        "reach_points": reach_used,                  # realistic travel distance in this direction
        "reach_reason": reach_reason,                # which limit was the binding one
        "reach_to_risk": reach_to_risk,              # reachable distance / risk — the honest reward:risk
        "not_worth_it": not_worth_it,                # True = setup was real but no ticket was issued
        "blocked_reason": blocked_reason,            # "no_room" | "no_chain" | None — WHY it was held back
        "reach": reach,                              # full reachability breakdown (or None)
        "blockers": blockers,                        # plain-English reasons there's no trade right now
        "votes": votes,                              # each indicator's -1/0/+1 vote
        "threshold": threshold,                      # score needed to fire, at current strictness
        "voting": voting,                            # how many indicators actually had an opinion
        "abstained": abstained,                      # which ones sat it out
        "agree": agree,                              # votes in the leaning direction
        "dissent": dissent,                          # votes against it
        "min_agree": min_agree,
        "max_dissent": max_dissent,
        "adx_needed": adx_needed,
        "strictness": config.SIGNAL_STRICTNESS,
        "score": total,
        "max_score": max_total,
        "technical": tech,
        "option_chain": oi,
    }
