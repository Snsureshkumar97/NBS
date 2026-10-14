"""
tv_rating.py — TradingView's Technical Ratings as a live entry on an Indian index, with the Trend Rider's exit
================================================================================
The user, 13 Oct 2026, after tv_ratings_study.py ("are there any other trading tools like me that work on the votes...
which is best"), asked to build it: "okay". A third system for the Dashboard's per-index System selector
(TicketBook.system_for), offered only where it was measured better - Nifty and Bank Nifty (config.SYSTEM_INDICES) -
and chosen by no one until the user picks it (config.SYSTEM_DEFAULTS unchanged).

THE ENTRY - TradingView's published rating (in.tradingview.com/support/solutions/43000614331), on the 15-minute
candles: 15 moving-average votes and 11 oscillator votes (see ratings()), each +1 / 0 / -1; the MA rating and the
oscillator rating are the means of their votes, the overall rating the mean of the two. At a 15-minute close where the
overall rating is Strong Buy (over +0.5) and no ticket is open, buy the call; Strong Sell (under -0.5), the put - and
again after an exit while it stays Strong, as TradingView's own strategy enters (tv_ratings_study's "TV + OURS").
THE EXIT - the Trend Rider's, as it runs (config.TREND_RIDER, TREND_RIDER_EXIT "rules"): stop the last `swing` candles'
low (high), one target at target_r x the risk, the stop to T1 at T1 and to T2 at T2 once that 15-minute candle closes,
the Supertrend trail after T1, the 2-hour breakeven; out at T3, the stop or the day's close. Always that exit - the one
measured - whatever TREND_RIDER_EXIT is set to.

MEASURED (tv_ratings_study.py, 3 years to 11 Sep 2026, per lot after costs, first two years / held-out): Nifty
+267,670 / +143,011 against the Trend Rider's +157,331 / +70,163; Bank Nifty +181,285 / +76,234 against +118,299 /
+50,859 - better on each, in both periods, but with a deeper worst drop (Nifty 26,758 / 33,562 against 20,739 / 20,175;
Bank Nifty 68,139 / 53,236 against 48,095 / 34,680).
"""
import numpy as np
import pandas as pd

import trend_rider

BAR = pd.Timedelta(minutes=15)
ENTRY_WINDOW_S = trend_rider.ENTRY_WINDOW_S
STRONG, WEAK = 0.5, 0.1
MIN_CLOSED = 230                 # the 200-candle averages present (60 days of chart history are merged in when held)
_CACHE = {}
_LAST_READY = {}
COLS = ["Open", "High", "Low", "Close", "Volume"]


def ratings(df):
    """DataFrame of TradingView's Technical Ratings at each candle's close: ma, osc, all (-1..+1), atr (14), and the
    vote counts ma_buy / ma_sell / ma_n, osc_buy / osc_sell / osc_n. The rules as the help centre states them:
      moving averages - SMA and EMA 10/20/30/50/100/200, Hull 9, VWMA 20: buy when the MA is under the price; Ichimoku
        9/26/52: buy when lead A > lead B, base > lead A, conversion > base and price > conversion (the cloud as drawn
        under the candle - its spans from 25 candles back). An index has no
        volume, so its VWMA is not available and is left out of the mean - as on TradingView.
      oscillators - RSI 14 (< 30 and rising), Stochastic 14/3/3 (both < 20, K over D), CCI 20 (< -100 and rising), ADX
        14/14 (+DI > -DI, ADX > 20 and rising; the sell: +DI < -DI, ADX > 20 and falling), Awesome Oscillator (crosses
        0, or a saucer above it), Momentum 10 (rising), MACD 12/26/9 (over its signal), Stoch RSI 3/3/14/14 (both < 20,
        K over D, in a downtrend), Williams %R 14 (< -80 and rising), Bull Bear Power 13 (bear power < 0 and rising, in
        an uptrend), Ultimate Oscillator 7/14/28 (> 70); each sell the mirror. "Uptrend": the price over its 50 EMA (the
        page does not say; the Pine library lists a period of 50 for it)."""
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"].astype(float)
    sma = lambda s, n: s.rolling(n).mean()
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()
    rma = lambda s, n: s.ewm(alpha=1.0 / n, adjust=False).mean()
    wts = {}

    def wma(s, n):
        w = wts.setdefault(n, np.arange(1, n + 1, dtype=float))
        return s.rolling(n).apply(lambda x: (x * w).sum() / w.sum(), raw=True)

    def vote(buy, sell, *need):
        out = pd.Series(np.where(buy, 1.0, np.where(sell, -1.0, 0.0)), index=df.index)
        bad = pd.Series(False, index=df.index)
        for s in need:
            bad |= s.isna()
        return out.mask(bad)

    up1 = lambda s: s > s.shift(1)
    dn1 = lambda s: s < s.shift(1)
    ma_votes = []
    for n in (10, 20, 30, 50, 100, 200):
        for m in (sma(c, n), ema(c, n).where(c.notna().cumsum() >= n)):
            ma_votes.append(vote(m < c, m > c, m))
    hull = wma(2 * wma(c, 4) - wma(c, 9), 3)
    ma_votes.append(vote(hull < c, hull > c, hull))
    vsum = v.rolling(20).sum()
    vwma = ((c * v).rolling(20).sum() / vsum).where(vsum > 0)
    ma_votes.append(vote(vwma < c, vwma > c, vwma))
    don = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2
    conv, base = don(9), don(26)
    # The cloud as TradingView draws it under today's candle: the spans plotted 26 candles ahead (Pine's offset
    # displacement - 1 = 25), i.e. computed 25 candles ago. Unshifted, "base > span A" and "conversion > base" could
    # never both hold (span A is their average) - caught by tv_rating_test.py on 13 Oct 2026.
    lead1, lead2 = ((conv + base) / 2).shift(25), don(52).shift(25)
    ma_votes.append(vote((lead1 > lead2) & (base > lead1) & (conv > base) & (c > conv),
                         (lead1 < lead2) & (base < lead1) & (conv < base) & (c < conv), lead1, lead2))
    trend_ma = ema(c, 50)
    uptrend, downtrend = c > trend_ma, c < trend_ma
    d = c.diff()
    rsi = 100 - 100 / (1 + rma(d.clip(lower=0), 14) / rma((-d).clip(lower=0), 14))
    hh14, ll14 = h.rolling(14).max(), l.rolling(14).min()
    k = sma(100 * (c - ll14) / (hh14 - ll14), 3)
    dd = sma(k, 3)
    cci_mean = sma(c, 20)
    cci_dev = c.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (c - cci_mean) / (0.015 * cci_dev)
    upm, dnm = h.diff(), -l.diff()
    pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0), index=df.index)
    mdm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr14 = rma(tr, 14)
    pdi, mdi = 100 * rma(pdm, 14) / atr14, 100 * rma(mdm, 14) / atr14
    adx = rma(100 * (pdi - mdi).abs() / (pdi + mdi), 14)
    hl2 = (h + l) / 2
    ao = sma(hl2, 5) - sma(hl2, 34)
    a1, a2 = ao.shift(1), ao.shift(2)
    mom = c - c.shift(10)
    macd = ema(c, 12) - ema(c, 26)
    sig = ema(macd, 9)
    rmin, rmax = rsi.rolling(14).min(), rsi.rolling(14).max()
    sk = sma(100 * (rsi - rmin) / (rmax - rmin), 3)
    sd = sma(sk, 3)
    wr = 100 * (c - hh14) / (hh14 - ll14)
    e13 = ema(c, 13)
    bull, bear = h - e13, l - e13
    pc = c.shift(1)
    bp = c - pd.concat([l, pc], axis=1).min(axis=1)
    trr = pd.concat([h, pc], axis=1).max(axis=1) - pd.concat([l, pc], axis=1).min(axis=1)
    avg = lambda n: bp.rolling(n).sum() / trr.rolling(n).sum()
    uo = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
    osc_votes = [
        vote((rsi < 30) & up1(rsi), (rsi > 70) & dn1(rsi), rsi, rsi.shift(1)),
        vote((k < 20) & (dd < 20) & (k > dd), (k > 80) & (dd > 80) & (k < dd), k, dd),
        vote((cci < -100) & up1(cci), (cci > 100) & dn1(cci), cci, cci.shift(1)),
        vote((pdi > mdi) & (adx > 20) & up1(adx), (pdi < mdi) & (adx > 20) & dn1(adx), adx, adx.shift(1)),
        vote(((ao > 0) & (a1 <= 0)) | ((ao > 0) & (a1 > 0) & (ao > a1) & (a2 > a1)),
             ((ao < 0) & (a1 >= 0)) | ((ao < 0) & (a1 < 0) & (ao < a1) & (a2 < a1)), ao, a2),
        vote(up1(mom), dn1(mom), mom, mom.shift(1)),
        vote(macd > sig, macd < sig, macd, sig),
        vote(downtrend & (sk < 20) & (sd < 20) & (sk > sd), uptrend & (sk > 80) & (sd > 80) & (sk < sd), sk, sd),
        vote((wr < -80) & up1(wr), (wr > -20) & dn1(wr), wr, wr.shift(1)),
        vote(uptrend & (bear < 0) & up1(bear), downtrend & (bull > 0) & dn1(bull), bear.shift(1), trend_ma),
        vote(uo > 70, uo < 30, uo),
    ]
    mv, ov = pd.concat(ma_votes, axis=1), pd.concat(osc_votes, axis=1)
    ma, osc = mv.mean(axis=1, skipna=True), ov.mean(axis=1, skipna=True)
    return pd.DataFrame({"ma": ma, "osc": osc, "all": (ma + osc) / 2, "atr": atr14,
                         "ma_buy": (mv > 0).sum(axis=1), "ma_sell": (mv < 0).sum(axis=1), "ma_n": mv.notna().sum(axis=1),
                         "osc_buy": (ov > 0).sum(axis=1), "osc_sell": (ov < 0).sum(axis=1),
                         "osc_n": ov.notna().sum(axis=1)})


def label(x):
    """TradingView's words for a rating."""
    if x is None or x != x:
        return "—"
    return ("Strong Buy" if x > STRONG else "Buy" if x > WEAK else "Strong Sell" if x < -STRONG
            else "Sell" if x < -WEAK else "Neutral")


def evaluate(index_key, df, now=None, history=None):
    """The reading on the last CLOSED candle of `df` (the last row is the forming candle), `history` (the feed's deeper
    chart window, when it has one) merged in before it so the 200-candle averages are settled: {ready, side (+1 / -1 /
    0 - the overall rating Strong at that close), stop, close, bar_close, fresh, values}."""
    p = trend_rider.params()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if df is None or len(df) < 3:
        return {"ready": False, "why": "no candles yet"}
    if df.index[-1] + BAR <= now:
        prev = _LAST_READY.get(index_key)
        why = "waiting for the new 15-minute candle's first prices"
        return dict(prev, ready=False, why=why, side=0, fresh=False) if prev else {"ready": False, "why": why}
    closed = df.iloc[:-1][COLS]
    key = (index_key, closed.index[-1], float(closed["Close"].iloc[-1]), len(closed), p["swing"])
    out = _CACHE.get(key)
    if out is None:
        full = closed
        if history is not None and len(history):
            older = history[history.index < closed.index[0]][COLS]
            if len(older):
                full = pd.concat([older, closed])
        if len(full) < MIN_CLOSED:
            return {"ready": False, "why": f"only {len(full)} closed candles - {MIN_CLOSED} needed for the 200-candle "
                                           "averages"}
        r = ratings(full).iloc[-1]
        a = float(r["all"])
        side = 1 if a > STRONG else -1 if a < -STRONG else 0
        w = int(p["swing"])
        lo, hi = float(closed["Low"].iloc[-w:].min()), float(closed["High"].iloc[-w:].max())
        c = float(closed["Close"].iloc[-1])
        values = {"close": c, "all": a, "ma": float(r["ma"]), "osc": float(r["osc"]),
                  "ma_buy": int(r["ma_buy"]), "ma_sell": int(r["ma_sell"]), "ma_n": int(r["ma_n"]),
                  "osc_buy": int(r["osc_buy"]), "osc_sell": int(r["osc_sell"]), "osc_n": int(r["osc_n"]),
                  "swing_low": lo, "swing_high": hi, "candles": len(full)}
        out = {"ready": True, "side": side, "stop": lo if side > 0 else hi if side < 0 else None, "close": c,
               "bar_close": (closed.index[-1] + BAR).isoformat(), "values": values}
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = out
    out = dict(out, fresh=(now - pd.Timestamp(out["bar_close"])).total_seconds() <= ENTRY_WINDOW_S)
    _LAST_READY[index_key] = out
    return out


def _conditions(ev):
    """The card's rows: the overall rating (the one condition), and the two groups behind it."""
    v = (ev or {}).get("values") or {}
    if not (ev or {}).get("ready") or v.get("all") is None:
        return [], 0
    a, ma, osc = v["all"], v["ma"], v["osc"]
    lean = 1 if a >= 0 else -1
    up = lean > 0
    word = "Strong Buy (over +0.5)" if up else "Strong Sell (under -0.5)"
    grp = lambda b, s, n: f"{b} buy · {s} sell · {n - b - s} neutral"
    return [
        ("tv_all", "Overall rating", (a > STRONG) if up else (a < -STRONG), f"{a:+.2f} {label(a)}",
         (f"TradingView's overall rating is {a:+.2f} - {label(a)}: the trade's one condition.",
          f"TradingView's overall rating is {a:+.2f} - {label(a)}; a trade needs {word}.")),
        ("tv_ma", "Moving averages", (ma > 0) if up else (ma < 0), f"{ma:+.2f} · {grp(v['ma_buy'], v['ma_sell'], v['ma_n'])}",
         (f"The {v['ma_n']} moving-average votes average {ma:+.2f} ({label(ma)}) - half of the overall rating.",
          f"The {v['ma_n']} moving-average votes average {ma:+.2f} ({label(ma)}) - half of the overall rating.")),
        ("tv_osc", "Oscillators", (osc > 0) if up else (osc < 0),
         f"{osc:+.2f} · {grp(v['osc_buy'], v['osc_sell'], v['osc_n'])}",
         (f"The {v['osc_n']} oscillator votes average {osc:+.2f} ({label(osc)}) - the other half.",
          f"The {v['osc_n']} oscillator votes average {osc:+.2f} ({label(osc)}) - the other half.")),
    ], lean


def apply(rec, ev, index_key, avoid_strikes=None):
    """Write the reading into the engine's recommendation for this index - the card's rows, and on a Strong rating at a
    close the side, the strike, its premium, the stop and the one target a ticket freezes (trend_rider.place, the
    Trend Rider's own levels). Anything incomplete is no trade, said in words."""
    p = trend_rider.params()
    rows, lean = _conditions(ev)
    v = (ev or {}).get("values") or {}
    info = {"label": "TradingView rating", "system": "tv_rating", "ready": bool((ev or {}).get("ready")),
            "why": (ev or {}).get("why"), "side": 0, "bar_close": (ev or {}).get("bar_close"),
            "fresh": bool((ev or {}).get("fresh")), "values": v, "votes": [], "no_cooldown": True,
            "filters": [{"key": k, "name": n, "ok": ok, "reading": rd, "texts": t} for k, n, ok, rd, t in rows],
            "target_r": p["target_r"], "swing": p["swing"], "stop_atr": None,
            "tag": "TradingView rating · " + label(v.get("all")),
            "note": (f"TradingView rating: TradingView's Technical Ratings - 26 indicators vote buy / sell / neutral (15 "
                     "moving averages, 11 oscillators). A trade at any 15-minute close where the overall rating is Strong "
                     "Buy (over +0.5) or Strong Sell (under -0.5) and no ticket is open - again after an exit while it "
                     f"stays Strong. Stop at the last {p['swing']} candles' low (high), one target at {p['target_r']:g} x "
                     "the risk; the stop moves to T1 at T1 and to T2 at T2 once that 15-minute candle closes, then follows "
                     "the Supertrend; to the entry after 2 hours without T1 - the Trend Rider's exit.")}
    return trend_rider.place(rec, info, ev, p, avoid_strikes,
                             f"TradingView rating {label(v.get('all'))} on the 15-minute close",
                             lambda side, spot, stop: _why_not(ev, side, spot, stop))


def _why_not(ev, side, spot, stop):
    if not (ev or {}).get("ready"):
        return [f"Waiting - {(ev or {}).get('why') or 'no reading yet'}."]
    if side and (spot is None or stop is None):
        return ["No price to measure the stop from yet."]
    if side:
        return [f"The rating is {label(((ev or {}).get('values') or {}).get('all'))} at the close but price has already "
                f"gone past its stop ({stop:,.2f}) - no trade."]
    a = ((ev or {}).get("values") or {}).get("all")
    return [f"TradingView's overall rating is {a:+.2f} ({label(a)}) on the last 15-minute close - a trade needs Strong "
            "Buy (over +0.5) or Strong Sell (under -0.5)."]
