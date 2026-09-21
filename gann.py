"""
gann.py — Gann Square of Nine levels and a volume oscillator, for the page and the bot
================================================================================
Asked for by the user on 20 Sep 2026. A calculator, not a signal: the levels are
shown on the Gann tab and handed to Ask TradePicker, and nothing in the rule
engine reads them. gann_volume_study.py tested both ideas as entry filters on
three years of 15-minute candles (20 Sep 2026) and neither improved the live
rules on total in both periods; that verdict is repeated on the tab so the
numbers are read as reference levels, not as an edge.

THE SQUARE OF NINE
    Integers spiralled outward from 1; the numbers on any one ray from the
    centre grow such that their square roots step by a fixed amount per turn:
    a full 360-degree turn adds 2 to sqrt(n), so 180 degrees adds 1, 90 adds
    0.5 and 45 adds 0.25. From a price P the levels "one step up" and "one step
    down" at angle a are (sqrt(P) +/- a/180)^2. Traders watch the 45-degree
    grid intraday and the 90/180-degree rungs as the bigger supports and
    resistances.

THE VOLUME OSCILLATOR
    (EMA5 of volume - EMA20 of volume) / EMA20 of volume, in percent. Above
    zero, participation is rising; below, fading. An index prints no volume,
    so the figure for Nifty, Bank Nifty and Sensex is on the near-month
    future's volume, which the candles carry when Zerodha is connected; for
    Bitcoin it is the perpetual's own.
"""
import math

import pandas as pd

STEP_DEG = 45                      # the grid shown
ANGLES = (45, 90, 180, 360)        # the rungs named on the tab
RUNGS = 4                          # steps of the grid each side of the price
VO_FAST, VO_SLOW = 5, 20
STUDY_NOTE = ("Tested 20 Sep 2026 on three years of 15-minute candles (gann_volume_study.py): entries filtered by "
              "these levels, or by a rising volume oscillator, did not beat the tool's live rules in both the "
              "in-sample and the held-out year. Reference levels, not part of the signal.")


def level(price, degrees):
    """The price `degrees` up (positive) or down (negative) the square from `price`."""
    r = math.sqrt(max(float(price), 1e-9)) + degrees / 180.0
    return round(r * r, 2) if r > 0 else 0.0


def grid(price, step_deg=STEP_DEG):
    """(next level below, next level above) the price on the step_deg grid,
    counted from the square's own rungs rather than from the price itself."""
    r = math.sqrt(max(float(price), 1e-9))
    inc = step_deg / 180.0
    base = math.floor(r / inc) * inc
    below, above = base * base, (base + inc) ** 2
    if below >= price:
        below = (base - inc) ** 2
    if above <= price:
        above = (base + 2 * inc) ** 2
    return round(below, 2), round(above, 2)


def ladder(price, step_deg=STEP_DEG, rungs=RUNGS):
    """The grid's rungs either side of the price, nearest first on each side,
    each with its angle on the square and whether it sits on a cardinal
    (a multiple of 90 degrees), which traders weight more."""
    r = math.sqrt(max(float(price), 1e-9))
    inc = step_deg / 180.0
    base = math.floor(r / inc) * inc
    out = []
    for k in range(-rungs + 1, rungs + 1):
        rr = base + k * inc
        if rr <= 0:
            continue
        deg = round(rr * 180.0)                      # the rung's angle on the square
        out.append({"price": round(rr * rr, 2), "angle": deg % 360, "cardinal": deg % 90 == 0,
                    "side": "above" if rr * rr > price else "below",
                    "pct_from_spot": round((rr * rr - price) / price * 100.0, 3)})
    return out


def volume_oscillator(volume, fast=VO_FAST, slow=VO_SLOW):
    """The series, in percent; NaN where the slow average is zero."""
    v = pd.Series(volume, dtype=float)
    ef, es = v.ewm(span=fast, adjust=False).mean(), v.ewm(span=slow, adjust=False).mean()
    return (ef - es) / es.replace(0, float("nan")) * 100.0


def _crypto(index):
    """True for an instrument on the crypto/Delta side, whose volume is the
    perpetual's (an Indian index's is the near-month future's)."""
    try:
        import config
        return (config.INSTRUMENTS.get(index) or {}).get("market") == "crypto"
    except Exception:
        return index == "BTC"


def report(index, spot, df=None):
    """Everything the Gann tab and the bot's get_gann show for one index."""
    if spot is None:
        return {"index": index, "spot": None, "note": "No live price yet.", "study": STUDY_NOTE}
    spot = float(spot)
    below, above = grid(spot)
    out = {"index": index, "spot": spot,
           "grid_degrees": STEP_DEG,
           "nearest_support": below, "nearest_resistance": above,
           "rungs": [{"angle": a, "up": level(spot, a), "down": level(spot, -a)} for a in ANGLES],
           "ladder": ladder(spot),
           "study": STUDY_NOTE}
    atr = None
    if df is not None and len(df) >= 15 and {"High", "Low", "Close"} <= set(df.columns):
        try:
            import indicators
            atr = float(indicators.atr(df, 14).iloc[-1])
        except Exception:
            atr = None
    if atr and atr == atr and atr > 0:
        out["atr14"] = round(atr, 2)
        out["resistance_in_atr"] = round((above - spot) / atr, 2)
        out["support_in_atr"] = round((spot - below) / atr, 2)
    vo = None
    if df is not None and "Volume" in df.columns and (df["Volume"] > 0).any():
        s = volume_oscillator(df["Volume"].to_numpy())
        last = s.dropna()
        if len(last):
            val = float(last.iloc[-1])
            prev = float(last.iloc[-2]) if len(last) > 1 else None
            vo = {"value_pct": round(val, 1), "rising": val > 0, "fast": VO_FAST, "slow": VO_SLOW,
                  "previous_bar_pct": round(prev, 1) if prev is not None else None,
                  "recent": [round(float(x), 1) for x in last.iloc[-24:]],
                  "volume_of": "the perpetual" if _crypto(index) else "the near-month future"}
    out["volume_oscillator"] = vo or {"value_pct": None,
                                     "note": ("No volume on this index's candles yet - it arrives from the "
                                              "near-month future once Zerodha is connected.")}
    return out
