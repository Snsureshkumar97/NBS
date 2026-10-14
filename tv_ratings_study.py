#!/usr/bin/env python3
"""
tv_ratings_study.py — TradingView's "Technical Ratings" (a 26-indicator vote) against the tool, all four indices, 3 years
================================================================================
The user, 13 Oct 2026: "i want to know are there any other trading tools like me that work on the votes and want to
know what is the accuracy of those i want to see a comparision of mine and their which is best".

The best-known vote tool with PUBLISHED rules is TradingView's Technical Ratings (the "Strong Buy / Buy / Neutral /
Sell / Strong Sell" speedometer, also its Screener's Rating column and the built-in "Technical Ratings Strategy").
Barchart's Opinion and Investing.com's technical summary are votes too, but their exact rules are not published; no
vendor publishes an accuracy figure for any of them. So the comparison is a port, run through the tool's own test.

THE RATINGS, as TradingView's help centre states them (in.tradingview.com/support/solutions/43000614331), on the
15-minute candles, each indicator a vote of +1 / 0 / -1:
  15 moving averages - SMA and EMA 10/20/30/50/100/200, Hull 9, VWMA 20 (buy when the MA is under the price), Ichimoku
     9/26/52 (buy when lead A > lead B, base > lead A, conversion > base, price > conversion; sell the mirror).
     The indices have no volume, so VWMA is not available and is left out of the average - as on TradingView.
  11 oscillators - RSI 14 (buy < 30 and rising), Stochastic 14/3/3 (both < 20, K over D), CCI 20 (< -100 and rising),
     ADX 14/14 (+DI > -DI, ADX > 20 and rising; sell +DI < -DI, ADX > 20 and falling, as the page words it), Awesome
     Oscillator (crosses 0, or a saucer above it), Momentum 10 (rising), MACD 12/26/9 (over its signal), Stoch RSI
     3/3/14/14 (both < 20, K over D, in a downtrend), Williams %R 14 (< -80 and rising), Bull Bear Power 13 (bear power
     < 0 and rising in an uptrend), Ultimate Oscillator 7/14/28 (> 70 buy, < 30 sell); the sells mirrored. "Uptrend" is
     the price over its 50 EMA - the page does not define it; the Pine library's Bull Bear Power lists a period of 50.
  MA rating = mean of the MA votes, oscillator rating = mean of the oscillator votes, overall = mean of the two.
  Strong Buy above +0.5, Strong Sell below -0.5.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026), per index, at the money on the real expiry (Midcap monthly), 0.25%
slippage a side, every charge, per lot (vwap_ema_atr_study.price_trade - every study's pricing):
  TV OWN      TradingView's built-in Technical Ratings Strategy (support/solutions/43000628415): Strong Buy -> buy the
              call, Strong Sell -> the put, whenever flat at a 15-minute close (Pine re-enters while the signal holds);
              stop 3 x ATR(14); once 5 x ATR in profit, a trailing stop 2 x ATR behind the best price; an opposite
              Strong signal reverses. Adapted for intraday option buying: out by 15:15, no new entry after 15:00.
              Three ways, as its "Rating is based on" input allows: All (the main one), MAs only, Oscillators only.
  TV + OURS   TradingView's All rating for the entries, the Trend Rider's exit as it runs live (stop the last 5 candles'
              low/high, target 2.75R, the stop to T1 / T2 once the touching 15-minute candle closes, the Supertrend
              trail after T1, the 2-hour breakeven) - whose VOTES pick better trades, the exit held the same.
  THE TOOL    as it runs today: the Trend Rider on its rules' exit (Nifty, Bank Nifty - this file's walk, asserted to
              reproduce trend_rider_rules_stop_study's B), the rules out at T3 with the T1-exit re-entry and the step at
              the candle close (Sensex - t1_step_timing_study's FIX), the rules out at T3 with the re-entry (Midcap -
              t3_exit_recheck_study's; no 5-minute history).
Walked on the 5-minute candles where there are 3 years of them (Nifty, Bank Nifty, Sensex), on the 15-minute candles
for Midcap - the same as the tool's own figure for that index. "Accuracy" two ways: trades won after costs, and how
often the index had moved the signal's way one hour after the entry (the plain "was the call right" a vendor means).

ADDED AFTER THE FIRST RUN, to explain why TV + OURS led on Nifty and Bank Nifty (TradingView re-enters while the signal
holds, the Trend Rider only on a fresh one - more trades, or better votes?): TradingView's votes on a fresh signal only,
and the Trend Rider re-entering while its condition holds - each with the same exit, so like is set against like.

RESULT (13 Oct 2026, history to 11 Sep 2026; tv_ratings_study_results.txt), all four pooled, first two years / held-out:
  THE TOOL                      +592,784 / +323,517   PF 1.26 / 1.26   worst drop  81,087 /  77,460   won 38% / 36%
  TradingView's own strategy    +773,635 / +296,107   PF 1.26 / 1.18   worst drop 156,507 / 153,789   won 43% / 45%
    - DROP: more in the first two years, less in the held-out year, about twice the worst drop; Midcap +16,203 /
      +53,955 against the rules' +194,339 / +94,122. MAs only the same picture (+1,067,646 / +302,841, drop 207,504).
      Oscillators only almost never reach Strong: 9 trades in 3 years.
  TV votes + the tool's exit    +748,556 / +373,907   PF 1.26 / 1.26   worst drop 111,799 / 117,403   - ahead both periods
    pooled, but only from Nifty and Bank Nifty: Sensex +272,193 / +77,586 (mixed against +122,814 / +108,374), Midcap
    +51,362 / +75,797 (worse both).
  LIKE FOR LIKE on Nifty + Bank Nifty, the Trend Rider's live exit on both: TradingView's votes beat the Trend Rider's
    both ways - fresh signals +67,309 / +80,398, while it holds +51,077 / +100,844 - and on each index alone, in both
    periods; but with a deeper worst drop (+22,874 / +19,597 at best). A candidate entry for those two, not built.
  "Accuracy": the index had moved the call's way an hour later on 50-55% of entries for EVERY system here, the tool's
  52% / 52% included - no vote system calls direction much better than a coin; the money is made by the exit.

    python3 tv_ratings_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
FIVE_MIN = ("NIFTY", "BANKNIFTY", "SENSEX")
STRONG = 0.5
B_FIGS = {"NIFTY": (157331, 70163), "BANKNIFTY": (118299, 50859)}     # trend_rider_rules_stop_study_results.txt, B
SENSEX_FIX = (122814, 108374)                                          # t1_step_timing_study_results.txt, FIX
MIDCAP_T3RE = (194339, 94122)                                          # t3_exit_recheck_study_results.txt, MIDCPNIFTY


# ---------------------------------------------------------------- TradingView's ratings
def tv_ratings(df):
    """DataFrame (ma, osc, all) of TradingView's Technical Ratings at each candle's close."""
    import numpy as np
    import pandas as pd
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
    # moving averages: buy when the MA is under the price
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
    lead1, lead2 = (conv + base) / 2, don(52)
    ma_votes.append(vote((lead1 > lead2) & (base > lead1) & (conv > base) & (c > conv),
                         (lead1 < lead2) & (base < lead1) & (conv < base) & (c < conv), lead1, lead2))
    # oscillators
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
    ma = pd.concat(ma_votes, axis=1).mean(axis=1, skipna=True)
    osc = pd.concat(osc_votes, axis=1).mean(axis=1, skipna=True)
    return pd.DataFrame({"ma": ma, "osc": osc, "all": (ma + osc) / 2, "atr": atr14})


# ---------------------------------------------------------------- one trade's walk
def walk(bars, t, start, bar_min, side, entry, stop, target, rungs, st_at, be_at, atr_trail, rev_at, cutoff):
    """trend_rider_rules_stop_study.walk's order of play on candles of bar_min minutes (5 or 15): pending stop steps
    take effect, the stop as it stood, the target, the rungs touched [(trigger, stop-to)] - each from the next
    15-minute candle - the Supertrend trail once a step has taken effect, the 2-hour breakeven (be_at; None = off) while
    no rung was touched; plus, for TradingView's own exit: atr_trail (activate, offset) behind the best price, and
    rev_at(end time) True = an opposite Strong signal at that 15-minute close (out at the close). -> (px, bar, via)."""
    import pandas as pd
    o, h, l, c, day = bars
    ce = side == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    done = [False] * len(rungs)
    pend = []
    first_live = touched = be_done = False
    best = entry
    step = pd.Timedelta(minutes=bar_min)
    d0 = day[start]
    j = start
    for j in range(start, len(c)):
        if day[j] != d0:
            return c[j - 1], j - 1, "square-off"
        for item in [x for x in pend if x[0] <= t[j]]:
            stop = tighten(stop, item[1])
            first_live = True
            pend.remove(item)
        if (l[j] <= stop) if ce else (h[j] >= stop):
            return (min(o[j], stop) if ce else max(o[j], stop)), j, "stop"
        if target is not None and ((h[j] >= target) if ce else (l[j] <= target)):
            return (max(o[j], target) if ce else min(o[j], target)), j, "target"
        for n, (trig, to) in enumerate(rungs):
            if done[n] or not ((h[j] >= trig) if ce else (l[j] <= trig)):
                continue
            done[n] = True
            touched = True
            pend.append((t[j].floor("15min") + pd.Timedelta(minutes=15), to))
        if st_at is not None and first_live:
            s = st_at(j)
            if s == s:
                stop = tighten(stop, s)
        if atr_trail is not None:
            best = max(best, h[j]) if ce else min(best, l[j])
            act, off = atr_trail
            if (best - entry if ce else entry - best) >= act:
                stop = tighten(stop, best - off if ce else best + off)
        end = t[j] + step
        if be_at is not None and not touched and not be_done and end >= be_at and end.minute % 15 == 0:
            be_done = True
            if (c[j] <= entry) if ce else (c[j] >= entry):
                return c[j], j, "breakeven-close"
            stop = tighten(stop, entry)
        if rev_at is not None and end.minute % 15 == 0 and rev_at(end):
            return c[j], j, "reverse"
        if cutoff[j]:
            return c[j], j, "square-off"
    return c[j], j, "square-off"


# ---------------------------------------------------------------- per index
def _tv_job(k):
    import datetime as dt
    import math
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import entry_timing_study as ets
    import index_candidates_study as ics
    import indicators as ind
    import pro_study as ps
    import regime_study as rs
    import trend_rider_t1_study as trs
    import vwap_ema_atr_study as vea
    ps.SLIP = 0.0025
    if k == "MIDCPNIFTY":                         # a fresh process per job: this patch dies with it
        real = ps._scheduled
        ps._scheduled = lambda kk, d, real=real: ics._monthly(d) if kk == k else real(kk, d)
    df = bt.fetch_history(k, years=3, use_cache=True)[["Open", "High", "Low", "Close", "Volume"]]
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    days = set(df.index.date)
    BAR15 = pd.Timedelta(minutes=15)
    rt = tv_ratings(df)
    st15 = ind.supertrend(df, *config.supertrend_params(k))[0].to_numpy()
    tod15 = np.array([x.time() for x in df.index])
    date15 = np.array([x.date() for x in df.index])
    last15 = (dt.datetime.combine(dt.date(2000, 1, 1), trs.OUT_BY) - dt.timedelta(minutes=15)).time()
    pos15 = {x: n for n, x in enumerate(df.index)}
    five = k in FIVE_MIN
    if five:
        d5 = ets.load_5m(k)
        t = d5.index
        bars = (d5["Open"].to_numpy(), d5["High"].to_numpy(), d5["Low"].to_numpy(), d5["Close"].to_numpy(),
                np.array([x.date() for x in t]))
        cut = np.array([x.time() >= (dt.datetime.combine(dt.date(2000, 1, 1), trs.OUT_BY)
                                     - dt.timedelta(minutes=5)).time() for x in t])
        p15 = df.index.searchsorted(t - BAR15, side="right") - 1
        st_at = lambda p: st15[p15[p]] if p15[p] >= 0 else np.nan
        bar_min = 5
    else:
        t = df.index
        bars = (df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy(), date15)
        cut = tod15 >= last15
        st_at = lambda j: st15[j]
        bar_min = 15
    RR, SWING = trs.RR, config.TREND_RIDER["swing"]
    lo, hi, cl = df["Low"].to_numpy(), df["High"].to_numpy(), df["Close"].to_numpy()

    def start_of(i):
        """The first candle of the walk after an entry at the close of 15-minute candle i (None: none that day)."""
        if not five:
            return i + 1
        p0 = int(t.searchsorted(df.index[i] + BAR15))
        return p0 if p0 < len(t) and t[p0].date() == date15[i] else None

    def bar15_of(p):                              # the 15-minute candle a walk candle belongs to
        return p if not five else int(df.index.searchsorted(t[p], side="right")) - 1

    def can_enter(i):
        return tod15[i] < last15 and date15[i + 1] == date15[i]

    def trade(i, side, stop, target, rungs, trail, be, atr_trail, rev_at):
        entry = cl[i]
        t_in = df.index[i] + BAR15
        p0 = start_of(i)
        if p0 is None:
            return None, None
        px, p, via = walk(bars, t, p0, bar_min, side, entry, stop, target, rungs, st_at if trail else None,
                          t_in + pd.Timedelta(minutes=120) if be else None, atr_trail, rev_at, cut)
        t_out = t[p] + pd.Timedelta(minutes=bar_min)
        s = sigma_by_day.get(t_in.date())
        net = vea.price_trade(k, days, t_in, entry, t_out, px, s, side == "CE") if s and s == s else None
        row = None if net is None else {"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via,
                                        "index": k, "entry_spot": entry}
        return row, bar15_of(p)

    def tr_exit(i, side):
        """The Trend Rider's live exit (TREND_RIDER_EXIT = "rules") from an entry at candle i: (stop, target, rungs)."""
        entry = cl[i]
        stop = lo[max(0, i - SWING + 1):i + 1].min() if side == "CE" else hi[max(0, i - SWING + 1):i + 1].max()
        risk = entry - stop if side == "CE" else stop - entry
        if risk <= 0:
            return None
        lv = lambda r: entry + r * risk if side == "CE" else entry - r * risk
        return stop, lv(RR), [(lv(RR / 3), lv(RR / 3)), (lv(RR * 2 / 3), lv(RR * 2 / 3))]

    out = {}
    # THE TOOL's Trend Rider on its live exit (Nifty / Bank Nifty): trend_rider_t1_study's entries and position-keeping
    if k in B_FIGS:
        rows, free = [], -1
        for i, side, entry, stop in trs.entries(df):
            if i <= free:
                continue
            got = tr_exit(i, side)
            row, x = trade(i, side, got[0], got[1], got[2], True, True, None, None)
            if x is None:
                continue
            if row:
                rows.append(row)
            free = x
        out["tool"] = rows
    # TradingView: whenever flat at a 15-minute close and the rating is Strong
    rating = {"all": rt["all"].to_numpy(), "ma": rt["ma"].to_numpy(), "osc": rt["osc"].to_numpy()}
    atr = rt["atr"].to_numpy()
    tr_long, tr_short = pc_signals(df)

    def side_of(basis, i, fresh):
        if basis == "trend_rider":                # the Trend Rider's own condition, while it holds
            return "CE" if tr_long[i] else "PE" if tr_short[i] else None
        r = rating[basis]
        if r[i] > STRONG and not (fresh and r[i - 1] > STRONG):
            return "CE"
        if r[i] < -STRONG and not (fresh and r[i - 1] < -STRONG):
            return "PE"
        return None

    variants = [("tv_all", "all", "own", False), ("tv_ma", "ma", "own", False), ("tv_osc", "osc", "own", False),
                ("tv_ours", "all", "ours", False),
                # added after the first run (13 Oct 2026), to explain tv_ours' lead: is it the votes, or more trades?
                ("tv_ours_fresh", "all", "ours", True)]
    if k in B_FIGS:
        variants.append(("tool_whenever", "trend_rider", "ours", False))
    for name, basis, mode, fresh in variants:
        r = rating.get(basis)
        rows, nxt = [], 200
        i = nxt
        while i < len(df) - 1:
            side = side_of(basis, i, fresh) if can_enter(i) else None
            if side is None:
                i += 1
                continue
            if mode == "own":
                a = atr[i]
                stop = cl[i] - 3 * a if side == "CE" else cl[i] + 3 * a
                opp = (lambda x: x < -STRONG) if side == "CE" else (lambda x: x > STRONG)
                rev_at = lambda end, opp=opp: (lambda n: n is not None and opp(r[n]))(pos15.get(end - BAR15))
                row, x = trade(i, side, stop, None, [], False, False, (5 * a, 2 * a), rev_at)
            else:
                got = tr_exit(i, side)
                if got is None:
                    i += 1
                    continue
                row, x = trade(i, side, got[0], got[1], got[2], True, True, None, None)
            if x is None:
                i += 1
                continue
            if row:
                rows.append(row)
            i = max(x, i + 1)                     # flat again from the close of the candle it left in (Pine)
        out[name] = rows
    # "was the call right": the index one hour (4 candles) after the entry, or the day's last close if sooner
    for rows in out.values():
        add_direction(rows, df)
    return k, out


def pc_signals(df):
    """The Trend Rider's entry condition at each candle (pasted_combos_study.signals, as trend_rider_t1_study uses it)."""
    import config
    import pasted_combos_study as pc
    return pc.signals(df, 2, config.TREND_RIDER["adx_min"])


def add_direction(rows, df):
    import numpy as np
    import pandas as pd
    cl = df["Close"].to_numpy()
    date = np.array([x.date() for x in df.index])
    pos = {x: n for n, x in enumerate(df.index)}
    for r in rows:
        i = pos[r["when"] - pd.Timedelta(minutes=15)]
        e = cl[i]
        j = i
        while j < i + 4 and j + 1 < len(cl) and date[j + 1] == date[i]:
            j += 1
        move = cl[j] - e
        r["right_1h"] = (move > 0) if r["side"] == "CE" else (move < 0)


def _sensex_job(_):
    import backtest_intraday as bt
    import t1_step_timing_study as tst
    _, rows = tst._job(("SENSEX", "FIX"))
    add_direction(rows, bt.fetch_history("SENSEX", years=3, use_cache=True))
    return "SENSEX", {"tool": rows}


def _midcap_job(_):
    import backtest_intraday as bt
    import t3_exit_recheck_study as t3
    _, out = t3._job("MIDCPNIFTY")
    rows = out["t3re"]
    add_direction(rows, bt.fetch_history("MIDCPNIFTY", years=3, use_cache=True))
    return "MIDCPNIFTY", {"tool": rows}


def _run(job):
    kind, arg = job
    return {"tv": _tv_job, "sensex": _sensex_job, "midcap": _midcap_job}[kind](arg)


def acc(rows):
    """(right one hour later %, first two years) and (held-out)."""
    import stop_day_study as sds
    a = [r for r in rows if r["when"] < sds.SPLIT]
    b = [r for r in rows if r["when"] >= sds.SPLIT]
    f = lambda x: 100.0 * sum(r["right_1h"] for r in x) / len(x) if x else 0.0
    return f(a), f(b)


def main():
    import multiprocessing as mp
    import stop_day_study as sds
    jobs = [("tv", k) for k in INDICES] + [("sensex", None), ("midcap", None)]
    with mp.get_context("spawn").Pool(processes=len(jobs), maxtasksperchild=1) as pool:
        res = pool.map(_run, jobs, chunksize=1)
    got = {k: {} for k in INDICES}
    for k, d in res:
        got[k].update(d)
    for k, (a, b) in list(B_FIGS.items()) + [("SENSEX", SENSEX_FIX), ("MIDCPNIFTY", MIDCAP_T3RE)]:
        s = sds.split(got[k]["tool"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: the tool's rows must be its live figures, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    label = {"tool": "THE TOOL as it runs today",
             "tv_all": "TradingView strategy, rating All",
             "tv_ma": "TradingView strategy, MAs only",
             "tv_osc": "TradingView strategy, oscillators only",
             "tv_ours": "TradingView All votes + the tool's exit",
             "tv_ours_fresh": "(info) TV votes, fresh signal only, tool exit",
             "tool_whenever": "(info) Trend Rider, re-entering while it holds"}
    order = ("tool", "tv_all", "tv_ma", "tv_osc", "tv_ours", "tv_ours_fresh", "tool_whenever")
    W = 190
    print("=" * W)
    print(" TRADINGVIEW TECHNICAL RATINGS (26-indicator vote) vs THE TOOL - at the money, real expiry, 0.25% slippage, every"
          " charge, per lot.  columns: trades, won, total, profit factor, worst drop, worst DAY; 'right 1h' = the index"
          " moved the call's way an hour later")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR{'':46s}| right 1h")
    pool_rows = lambda v: sorted([r for k in INDICES for r in got[k].get(v, [])], key=lambda r: r["when"])
    print(" ALL FOUR INDICES")
    for v in order:
        a, b = acc(pool_rows(v))
        print(sds.line("   " + label[v], pool_rows(v)) + f"  | {a:3.0f}% / {b:3.0f}%")
    for k in INDICES:
        print(f" {k}  ({'5' if k in FIVE_MIN else '15'}-minute walk)")
        for v in order:
            if v in got[k]:
                a, b = acc(got[k][v])
                print(sds.line("   " + label[v], got[k][v]) + f"  | {a:3.0f}% / {b:3.0f}%")
    print("\n VERDICT - TradingView better than the tool only if more profit in BOTH periods (all four pooled)")
    for v in order[1:6]:
        print("  " + sds.verdict(label[v], pool_rows(v), pool_rows("tool")).strip())
    tr2 = lambda v: sorted([r for k in B_FIGS for r in got[k].get(v, [])], key=lambda r: r["when"])
    print("\n WHOSE VOTES, LIKE FOR LIKE - Nifty + Bank Nifty, the same exit (the Trend Rider's live one) on both")
    for v in ("tool", "tv_ours_fresh", "tool_whenever", "tv_ours"):
        a, b = acc(tr2(v))
        print(sds.line("   " + label[v], tr2(v)) + f"  | {a:3.0f}% / {b:3.0f}%")
    print("  " + sds.verdict("fresh signals: TV votes vs the Trend Rider's", tr2("tv_ours_fresh"), tr2("tool")).strip())
    print("  " + sds.verdict("while it holds: TV votes vs the Trend Rider's", tr2("tv_ours"), tr2("tool_whenever")).strip())
    print("\n HOW THE TRADINGVIEW TRADES CLOSED (all four, rating All, its own exit)")
    from collections import Counter
    print("   " + ", ".join(f"{w}: {n}" for w, n in Counter(r["closed_via"] for r in pool_rows("tv_all")).most_common()))
    return got


if __name__ == "__main__":
    main()
