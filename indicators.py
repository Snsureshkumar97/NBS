"""
indicators.py
--------------
Pure pandas/numpy technical indicator functions — no external TA library
required (keeps the tool easy to install). All functions take/return
pandas Series aligned to the input OHLC DataFrame's index.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.fillna(50)  # neutral when undefined (e.g. flat start)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def supertrend(df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0):
    """
    Returns (supertrend_line, direction) where direction = 1 for uptrend,
    -1 for downtrend, matching the common convention used by most charting
    platforms (TradingView's built-in supertrend uses the opposite sign
    internally but the same visual result).
    """
    hl2 = (df["High"] + df["Low"]) / 2
    atr_val = atr(df, atr_period)

    upperband = hl2 + multiplier * atr_val
    lowerband = hl2 - multiplier * atr_val

    final_upper = upperband.copy()
    final_lower = lowerband.copy()
    direction = pd.Series(index=df.index, dtype=int)
    st = pd.Series(index=df.index, dtype=float)

    for i in range(len(df)):
        if i == 0:
            direction.iloc[i] = 1
            st.iloc[i] = final_lower.iloc[i]
            continue

        if upperband.iloc[i] < final_upper.iloc[i - 1] or df["Close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = upperband.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if lowerband.iloc[i] > final_lower.iloc[i - 1] or df["Close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = lowerband.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        if df["Close"].iloc[i] > final_upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["Close"].iloc[i] < final_lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and final_lower.iloc[i] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = final_lower.iloc[i - 1]
            if direction.iloc[i] == -1 and final_upper.iloc[i] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

        st.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == 1 else final_upper.iloc[i]

    return st, direction


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Session VWAP. Expects a DatetimeIndex. Resets at the start of each
    calendar day present in the data (fine for intraday bars; for daily
    bars this just becomes a running VWAP across the whole series).
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = typical_price * df["Volume"].replace(0, np.nan)
    day = df.index.date
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["Volume"].replace(0, np.nan).groupby(day).cumsum()
    result = cum_pv / cum_vol
    return result.ffill().fillna(typical_price)


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Average Directional Index — measures TREND STRENGTH (not direction).
    Roughly: 0-20 = weak/no trend (choppy, range-bound), 20-25 = developing,
    25+ = trending. Used as a GATE, not a vote: even if every other
    indicator agrees on a direction, a low ADX means that "agreement" is
    happening in a directionless, chop-prone market and is much more likely
    to whipsaw. Wilder's original smoothing, approximated the same way
    atr() does (ewm with alpha=1/length), for consistency with the rest of
    this file.
    """
    high, low = df["High"], df["Low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_mask = (up_move > down_move) & (up_move > 0)
    minus_mask = (down_move > up_move) & (down_move > 0)
    plus_dm[plus_mask] = up_move[plus_mask]
    minus_dm[minus_mask] = down_move[minus_mask]

    smoothed_tr = true_range(df).ewm(alpha=1 / length, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / smoothed_tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / smoothed_tr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(alpha=1 / length, adjust=False).mean()
    return adx_val.fillna(0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    MACD — returns (macd_line, signal_line, histogram). Unlike the EMA-trend
    check or Supertrend (both just "is price above/below a moving
    reference"), MACD's histogram measures MOMENTUM — is the move
    accelerating or decelerating — so it can diverge from raw trend
    direction near turning points instead of just re-confirming it.
    """
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
