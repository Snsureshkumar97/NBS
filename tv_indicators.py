"""
tv_indicators.py — Python ports of popular OPEN-SOURCE TradingView community indicators (default settings)
================================================================================
The user, 4 Oct 2026: "check all kind of indicators in the trading view of people who create their own which are
published on trading view for crypto market test all the indicators and pick one and compare with our startegy".
Ported from their published Pine logic, defaults as published. Each returns, per candle (decided at its CLOSE):
  entry   +1 a buy signal, -1 a sell signal, 0 none
  exit_long / exit_short   True where the indicator itself says a long / a short is over
(Pine's rma/atr seed differently in the first bars; nothing here is used before a long warm-up.)
"""
import numpy as np
import pandas as pd


def _rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def _tr(df):
    h, l, c = df["High"], df["Low"], df["Close"]
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)


def _from_state(state):
    """A trend STATE (+1 / -1 / 0) -> entries where it turns, exits where it is no longer that side."""
    s = np.asarray(state, dtype=int)
    prev = np.concatenate([[0], s[:-1]])
    entry = np.where((s != prev) & (s != 0), s, 0)
    return entry, s != 1, s != -1


def ut_bot(df, a=1.0, c=10):
    """UT Bot Alerts (QuantNomad): an ATR trailing stop; pos flips when the close crosses it."""
    src = df["Close"].to_numpy()
    n_loss = a * _rma(_tr(df), c).to_numpy()
    ts = np.zeros(len(src)); pos = np.zeros(len(src), dtype=int)
    for i in range(1, len(src)):
        p = ts[i - 1]
        if src[i] > p and src[i - 1] > p:
            ts[i] = max(p, src[i] - n_loss[i])
        elif src[i] < p and src[i - 1] < p:
            ts[i] = min(p, src[i] + n_loss[i])
        else:
            ts[i] = src[i] - n_loss[i] if src[i] > p else src[i] + n_loss[i]
        pos[i] = 1 if (src[i - 1] < p and src[i] > p) else -1 if (src[i - 1] > p and src[i] < p) else pos[i - 1]
    return _from_state(pos)


def ssl_channel(df, length=10):
    """SSL Channel (ErwinBeckers): close above the SMA of highs -> up, below the SMA of lows -> down."""
    c = df["Close"].to_numpy()
    sh, sl = df["High"].rolling(length).mean().to_numpy(), df["Low"].rolling(length).mean().to_numpy()
    hlv = np.zeros(len(c), dtype=int)
    for i in range(1, len(c)):
        hlv[i] = 1 if c[i] > sh[i] else -1 if c[i] < sl[i] else hlv[i - 1]
    return _from_state(hlv)


def _linreg_last(y, n):
    """Pine linreg(y, n, 0): the least-squares line over the last n values, at the newest."""
    y = pd.Series(y)
    x = np.arange(n, dtype=float)
    sx, sxx = x.sum(), (x * x).sum()
    sy = y.rolling(n).sum()
    sxy = y.rolling(n).apply(lambda w: float(np.dot(x, w)), raw=True)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    icpt = (sy - slope * sx) / n
    return (icpt + slope * (n - 1)).to_numpy()


def squeeze_momentum(df, length=20, mult=2.0, length_kc=20, mult_kc=1.5):
    """Squeeze Momentum (LazyBear): a buy / sell as the squeeze FIRES (Bollinger back outside Keltner), in the
    momentum's direction; out when the momentum turns (its histogram shrinking)."""
    c, h, l = df["Close"], df["High"], df["Low"]
    basis, dev = c.rolling(length).mean(), mult * c.rolling(length).std(ddof=0)
    ma, rangema = c.rolling(length_kc).mean(), _tr(df).rolling(length_kc).mean()
    sqz_on = ((basis - dev) > (ma - rangema * mult_kc)) & ((basis + dev) < (ma + rangema * mult_kc))
    mid = ((h.rolling(length_kc).max() + l.rolling(length_kc).min()) / 2 + c.rolling(length_kc).mean()) / 2
    val = _linreg_last((c - mid).to_numpy(), length_kc)
    fired = (sqz_on.shift(1, fill_value=False) & ~sqz_on).to_numpy()
    entry = np.where(fired & (val > 0), 1, np.where(fired & (val < 0), -1, 0))
    prev = np.concatenate([[np.nan], val[:-1]])
    return entry, val < prev, val > prev


def wavetrend(df, n1=10, n2=21, ob=53, os_=-53):
    """WaveTrend (LazyBear): buy when WT1 crosses up through WT2 below -53, sell when down above +53; out on the
    opposite cross."""
    ap = (df["High"] + df["Low"] + df["Close"]) / 3
    esa = ap.ewm(span=n1, adjust=False).mean()
    d = (ap - esa).abs().ewm(span=n1, adjust=False).mean()
    ci = (ap - esa) / (0.015 * d)
    wt1 = ci.ewm(span=n2, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()
    up = ((wt1 > wt2) & (wt1.shift() <= wt2.shift())).to_numpy()
    dn = ((wt1 < wt2) & (wt1.shift() >= wt2.shift())).to_numpy()
    w = wt1.to_numpy()
    entry = np.where(up & (w < os_), 1, np.where(dn & (w > ob), -1, 0))
    return entry, dn, up


def chandelier_exit(df, length=22, mult=3.0):
    """Chandelier Exit (everget, close-based): the trend flips when the close crosses the other side's stop."""
    c = df["Close"].to_numpy()
    atr = mult * _rma(_tr(df), length).to_numpy()
    hh, ll = df["Close"].rolling(length).max().to_numpy(), df["Close"].rolling(length).min().to_numpy()
    ls = hh - atr; ss = ll + atr
    d = np.ones(len(c), dtype=int)
    for i in range(1, len(c)):
        lp, sp = ls[i - 1], ss[i - 1]
        if not np.isnan(lp) and c[i - 1] > lp:
            ls[i] = max(ls[i], lp)
        if not np.isnan(sp) and c[i - 1] < sp:
            ss[i] = min(ss[i], sp)
        d[i] = 1 if (not np.isnan(sp) and c[i] > sp) else -1 if (not np.isnan(lp) and c[i] < lp) else d[i - 1]
    return _from_state(d)


def range_filter(df, per=100, mult=3.0):
    """Range Filter Buy and Sell (DonovanWall / guikroth): a filtered price; the signal when the close is on its
    side with the filter moving that way, on a change of condition."""
    src = df["Close"]
    avrng = (src - src.shift()).abs().ewm(span=per, adjust=False).mean()
    smrng = (avrng.ewm(span=per * 2 - 1, adjust=False).mean() * mult).to_numpy()
    x = src.to_numpy()
    filt = np.zeros(len(x)); up = np.zeros(len(x)); dn = np.zeros(len(x)); cond = np.zeros(len(x), dtype=int)
    for i in range(1, len(x)):
        p, r = filt[i - 1], smrng[i]
        if np.isnan(r):
            filt[i] = x[i]; continue
        filt[i] = (p if x[i] - r < p else x[i] - r) if x[i] > p else (p if x[i] + r > p else x[i] + r)
        up[i] = up[i - 1] + 1 if filt[i] > p else 0 if filt[i] < p else up[i - 1]
        dn[i] = dn[i - 1] + 1 if filt[i] < p else 0 if filt[i] > p else dn[i - 1]
        long_c = x[i] > filt[i] and up[i] > 0
        short_c = x[i] < filt[i] and dn[i] > 0
        cond[i] = 1 if long_c else -1 if short_c else cond[i - 1]
    prev = np.concatenate([[0], cond[:-1]])
    entry = np.where((cond == 1) & (prev == -1), 1, np.where((cond == -1) & (prev == 1), -1, 0))
    return entry, cond != 1, cond != -1


def halftrend(df, amplitude=2):
    """HalfTrend (everget): the trend turns down when the highs' average falls under the highest recent low and
    the close breaks the previous low (and up, mirrored)."""
    h, l, c = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    hp = df["High"].rolling(amplitude).max().to_numpy(); lp = df["Low"].rolling(amplitude).min().to_numpy()
    hma = df["High"].rolling(amplitude).mean().to_numpy(); lma = df["Low"].rolling(amplitude).mean().to_numpy()
    trend = np.zeros(len(c), dtype=int); nxt = 0
    max_low, min_high = l[0], h[0]
    for i in range(1, len(c)):
        trend[i] = trend[i - 1]
        if np.isnan(hp[i]):
            continue
        if nxt == 1:
            max_low = max(lp[i], max_low)
            if hma[i] < max_low and c[i] < l[i - 1]:
                trend[i], nxt, min_high = 1, 0, hp[i]
        else:
            min_high = min(hp[i], min_high)
            if lma[i] > min_high and c[i] > h[i - 1]:
                trend[i], nxt, max_low = 0, 1, lp[i]
    return _from_state(np.where(trend == 0, 1, -1))


def _wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def hull_suite(df, length=55):
    """Hull Suite (InSilico, HMA mode): up while the Hull MA is above its value 2 candles ago."""
    c = df["Close"]
    hma = _wma(2 * _wma(c, length // 2) - _wma(c, length), int(round(np.sqrt(length))))
    st = np.where(hma > hma.shift(2), 1, np.where(hma < hma.shift(2), -1, 0))
    return _from_state(pd.Series(st).replace(0, np.nan).ffill().fillna(0).astype(int).to_numpy())


def coral_trend(df, sm=21, cd=0.4):
    """Coral Trend (LazyBear): a 6-pole smoothed line; up while it rises, down while it falls."""
    di = (sm - 1.0) / 2.0 + 1.0
    c1 = 2 / (di + 1.0)
    i1 = df["Close"].ewm(alpha=c1, adjust=False).mean()
    i2 = i1.ewm(alpha=c1, adjust=False).mean(); i3 = i2.ewm(alpha=c1, adjust=False).mean()
    i4 = i3.ewm(alpha=c1, adjust=False).mean(); i5 = i4.ewm(alpha=c1, adjust=False).mean()
    i6 = i5.ewm(alpha=c1, adjust=False).mean()
    c3 = 3.0 * (cd * cd + cd * cd * cd); c4 = -3.0 * (2.0 * cd * cd + cd + cd * cd * cd)
    c5 = 3.0 * cd + 1.0 + cd * cd * cd + 3.0 * cd * cd
    bfr = -cd * cd * cd * i6 + c3 * i5 + c4 * i4 + c5 * i3
    st = np.where(bfr > bfr.shift(), 1, np.where(bfr < bfr.shift(), -1, 0))
    return _from_state(pd.Series(st).replace(0, np.nan).ffill().fillna(0).astype(int).to_numpy())


def supertrend(df, period=10, mult=3.0):
    """Supertrend (KivancOzbilgic defaults): the tool's own indicators.supertrend."""
    import indicators as ind
    return _from_state(ind.supertrend(df, period, mult)[1].to_numpy().astype(int))


ALL = {"UT Bot Alerts": ut_bot, "Squeeze Momentum": squeeze_momentum, "WaveTrend": wavetrend,
       "SSL Channel": ssl_channel, "Chandelier Exit": chandelier_exit, "Range Filter": range_filter,
       "HalfTrend": halftrend, "Hull Suite": hull_suite, "Coral Trend": coral_trend, "Supertrend": supertrend}
