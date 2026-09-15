"""
screener.py - build your own screen over the index member stocks
================================================================================
Pick conditions - RSI(14) below 30, CCI above 100, Williams %R below -80,
price above its Parabolic SAR, close above EMA(50), volume above 1.5x its
20-day average, a weekly close above a monthly moving average, MACD crossing
its signal - and see which Nifty, Bank Nifty and Sensex members match right now.

The condition format follows the open-source Indian-Stock-Market-Screener
project (github.com/Dharmik-Solanki-G/Indian-Stock-Market-Screener): a left
side that is an indicator, an operator, and a right side that is a number or
another indicator scaled by a multiplier plus an offset, each with a timeframe
and how many bars ago. Its strategy files load here as they are. Its natural-
language builder needs a local LLM and is not brought in; nothing here is
evaluated as code - every field is checked against fixed lists and ranges.

A screen shows what matches NOW. It says nothing about whether trading it makes
money, and the page says so. A stock without enough history for a condition is
left out rather than guessed.
"""
import json
import math
import os
import threading

import numpy as np
import pandas as pd

import indicators as ind
import trade_log

TIMEFRAMES = ("daily", "weekly", "monthly")
OPERATORS = (">", "<", ">=", "<=", "crosses above", "crosses below")
MAX_CONDITIONS = 8
MAX_OFFSET = 60
MAX_SAVED = 30
NAME_MAX = 60

_MACD = [("fast", 12, 2, 100), ("slow", 26, 3, 200), ("signal", 9, 2, 50)]
_BB = [("period", 20, 2, 200), ("stdev", 2, 0.5, 5)]
# name: (label, [(param, default, low, high)])
INDICATORS = {
    "close": ("Close", []), "open": ("Open", []), "high": ("High", []), "low": ("Low", []),
    "volume": ("Volume", []),
    "sma": ("SMA", [("period", 20, 1, 300)]),
    "ema": ("EMA", [("period", 20, 1, 300)]),
    "wma": ("WMA", [("period", 20, 1, 300)]),
    "rsi": ("RSI", [("period", 14, 2, 100)]),
    "cci": ("CCI", [("period", 20, 2, 200)]),
    "williams_r": ("Williams %R", [("period", 14, 2, 200)]),
    "macd": ("MACD line", _MACD), "macd_signal": ("MACD signal", _MACD),
    "macd_hist": ("MACD histogram", _MACD),
    "adx": ("ADX", [("period", 14, 2, 100)]),
    "atr": ("ATR", [("period", 14, 2, 100)]),
    "bb_high": ("Bollinger upper", _BB), "bb_mid": ("Bollinger middle", _BB),
    "bb_low": ("Bollinger lower", _BB),
    "psar": ("Parabolic SAR", [("step", 0.02, 0.005, 0.1), ("max_step", 0.2, 0.05, 0.5)]),
    "volume_sma": ("Volume SMA", [("period", 20, 1, 200)]),
}
# Settings that are fractions rather than bar counts.
_FLOAT_PARAMS = {"stdev", "step", "max_step"}


# ---------------------------------------------------------------------------
# checking a screen
# ---------------------------------------------------------------------------
def _num(value, name, lo, hi):
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} has to be a number.")
    if x != x or not lo <= x <= hi:
        raise ValueError(f"{name} has to be between {lo:g} and {hi:g}.")
    return x


def _clean_operand(o, side):
    if not isinstance(o, dict):
        raise ValueError("Each side of a condition has to be filled in.")
    kind = o.get("type")
    if kind == "value":
        if side == "lhs":
            raise ValueError("The left side of a condition has to be an indicator.")
        return {"type": "value", "value": _num(o.get("value"), "The number", -1e9, 1e9)}
    if kind != "indicator":
        raise ValueError("Each side is an indicator or a number.")
    name = str(o.get("name") or "").strip().lower()
    if name not in INDICATORS:
        raise ValueError(f"Unknown indicator “{name[:24]}”.")
    tf = str(o.get("tf", o.get("timeframe", "daily")) or "daily").lower()
    if tf not in TIMEFRAMES:
        raise ValueError("Timeframe is daily, weekly or monthly.")
    label = INDICATORS[name][0]
    src = o.get("params") or {}
    if not isinstance(src, dict):
        raise ValueError(f"{label}: the settings could not be read.")
    params = {}
    for key, default, lo, hi in INDICATORS[name][1]:
        raw = src.get(key, src.get(f"period_{key}"))
        v = default if raw in (None, "") else _num(raw, f"{label} {key}", lo, hi)
        params[key] = float(v) if key in _FLOAT_PARAMS else int(round(v))
    if "step" in params and params["step"] > params["max_step"]:
        raise ValueError("Parabolic SAR: the step cannot be larger than the maximum.")
    if "fast" in params and params["fast"] >= params["slow"]:
        raise ValueError("MACD: the fast period has to be shorter than the slow one.")
    out = {"type": "indicator", "name": name, "tf": tf, "params": params,
           "offset": int(_num(o.get("offset", 0) or 0, "Bars ago", 0, MAX_OFFSET))}
    if side == "rhs":
        out["mult"] = _num(o.get("mult", o.get("multiplier", 1)), "Multiplier", 0.01, 100)
        out["add"] = _num(o.get("add", o.get("add_offset", 0)), "Added amount", -1e9, 1e9)
    return out


def clean(strategy):
    """A screen as the builder sent it - or as a strategy file from the source
    project - checked and put into one shape. Raises ValueError."""
    if not isinstance(strategy, dict):
        raise ValueError("That screen could not be read.")
    name = str(strategy.get("name") or "My screen").strip()[:NAME_MAX] or "My screen"
    conds = strategy.get("conditions")
    if not isinstance(conds, list) or not conds:
        raise ValueError("A screen needs at least one condition.")
    if len(conds) > MAX_CONDITIONS:
        raise ValueError(f"A screen can have at most {MAX_CONDITIONS} conditions.")
    out = []
    for c in conds:
        if not isinstance(c, dict):
            raise ValueError("A condition could not be read.")
        op = str(c.get("op", c.get("operator", "")) or "").strip().lower()
        if op not in OPERATORS:
            raise ValueError(f"Unknown comparison “{op[:20]}”.")
        out.append({"lhs": _clean_operand(c.get("lhs"), "lhs"), "op": op,
                    "rhs": _clean_operand(c.get("rhs"), "rhs")})
    return {"name": name, "conditions": out}


# ---------------------------------------------------------------------------
# computing
# ---------------------------------------------------------------------------
_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def frames(df):
    """Daily candles (ts, open, high, low, close, volume) -> daily, weekly and
    monthly frames with the capitalised columns indicators.py expects."""
    d = df.copy()
    d["ts"] = pd.to_datetime(d["ts"])
    if getattr(d["ts"].dt, "tz", None) is not None:
        d["ts"] = d["ts"].dt.tz_localize(None)
    d = (d.sort_values("ts").set_index("ts")
          .rename(columns={"open": "Open", "high": "High", "low": "Low",
                           "close": "Close", "volume": "Volume"})
          [["Open", "High", "Low", "Close", "Volume"]].astype(float))
    try:
        monthly = d.resample("ME").agg(_AGG).dropna()
    except ValueError:
        monthly = d.resample("M").agg(_AGG).dropna()
    return {"daily": d, "weekly": d.resample("W-FRI").agg(_AGG).dropna(), "monthly": monthly}


def _series(fr, o, cache):
    key = (o["tf"], o["name"], tuple(sorted(o["params"].items())))
    if key in cache:
        return cache[key]
    df, n, p = fr[o["tf"]], o["name"], o["params"]
    c = df["Close"]
    warm = 0
    if n in ("close", "open", "high", "low", "volume"):
        s = df[n.capitalize()]
    elif n == "sma":
        s = c.rolling(p["period"]).mean()
    elif n == "ema":
        s, warm = ind.ema(c, p["period"]), p["period"]
    elif n == "wma":
        w = np.arange(1, p["period"] + 1, dtype=float)
        s = c.rolling(p["period"]).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)
    elif n == "rsi":
        s, warm = ind.rsi(c, p["period"]), p["period"] + 1
    elif n.startswith("macd"):
        line, sig, hist = ind.macd(c, p["fast"], p["slow"], p["signal"])
        s, warm = {"macd": line, "macd_signal": sig, "macd_hist": hist}[n], p["slow"] + p["signal"]
    elif n == "adx":
        s, warm = ind.adx(df, p["period"]), 2 * p["period"]
    elif n == "atr":
        s, warm = ind.atr(df, p["period"]), p["period"]
    elif n.startswith("bb_"):
        mid = c.rolling(p["period"]).mean()
        sd = c.rolling(p["period"]).std()
        s = {"bb_mid": mid, "bb_high": mid + p["stdev"] * sd, "bb_low": mid - p["stdev"] * sd}[n]
    elif n == "cci":
        # Lambert's CCI: typical price against its average, over 0.015 x the
        # mean absolute deviation from that average.
        tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
        sma = tp.rolling(p["period"]).mean()
        mad = tp.rolling(p["period"]).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
        s = (tp - sma) / (0.015 * mad.replace(0, np.nan))
    elif n == "williams_r":
        hh = df["High"].rolling(p["period"]).max()
        ll = df["Low"].rolling(p["period"]).min()
        s = -100.0 * (hh - c) / (hh - ll).replace(0, np.nan)
    elif n == "psar":
        s, warm = _psar(df["High"], df["Low"], p["step"], p["max_step"]), 3
    elif n == "volume_sma":
        s = df["Volume"].rolling(p["period"]).mean()
    else:
        raise ValueError(f"Unknown indicator {n}")
    if warm:
        # A smoothed indicator produces numbers from its first bar, before it has
        # seen enough of them to mean anything. Those count as not yet known.
        s = s.copy()
        s.iloc[:min(len(s), warm - 1)] = np.nan
    cache[key] = s
    return s


def _psar(high, low, step, max_step):
    """Wilder's Parabolic SAR. Below price in an uptrend, above it in a
    downtrend; the acceleration factor starts at `step`, grows by `step` each
    new extreme, is capped at `max_step`, and resets when the trend flips."""
    h, l = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
    n = len(h)
    out = np.full(n, np.nan)
    if n < 3:
        return pd.Series(out, index=high.index)
    up = h[1] >= h[0]
    ep = h[1] if up else l[1]
    sar = l[0] if up else h[0]
    af = step
    out[1] = sar
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        out[i] = sar
    return pd.Series(out, index=high.index)


def _value(fr, o, cache, back=0):
    if o["type"] == "value":
        return o["value"]
    s = _series(fr, o, cache)
    i = o["offset"] + back
    if len(s) <= i:
        return None
    v = s.iloc[-(1 + i)]
    if v is None or pd.isna(v):
        return None
    v = float(v)
    if "mult" in o:
        v = v * o["mult"] + o["add"]
    return v


def evaluate(fr, strategy):
    cache, detail, match, unknown = {}, [], True, False
    for c in strategy["conditions"]:
        lv, rv = _value(fr, c["lhs"], cache), _value(fr, c["rhs"], cache)
        ok = None
        if lv is not None and rv is not None:
            if c["op"] in ("crosses above", "crosses below"):
                lp, rp = _value(fr, c["lhs"], cache, 1), _value(fr, c["rhs"], cache, 1)
                if lp is not None and rp is not None:
                    ok = (lv > rv and lp <= rp) if c["op"] == "crosses above" else (lv < rv and lp >= rp)
            else:
                ok = {">": lv > rv, "<": lv < rv, ">=": lv >= rv, "<=": lv <= rv}[c["op"]]
        if ok is None:
            unknown = True
        match = match and bool(ok)
        detail.append({"lhs": None if lv is None else round(lv, 4),
                       "rhs": None if rv is None else round(rv, 4), "ok": ok})
    return {"match": match and not unknown, "unknown": unknown, "detail": detail}


def members():
    """symbol -> which indices it belongs to, and its sector."""
    import market_map
    out = {}
    for index, rows in market_map.CONSTITUENTS.items():
        for sym, sector, _w in rows:
            m = out.setdefault(sym, {"indices": [], "sector": sector})
            m["indices"].append(index)
    return out


def run(hist, strategy, member_map=None):
    strategy = clean(strategy)
    matches, checked, unknown, skipped = [], 0, [], []
    for sym, df in sorted((hist or {}).items()):
        if df is None or len(df) < 30:
            skipped.append(sym)
            continue
        try:
            fr = frames(df)
        except Exception:
            skipped.append(sym)
            continue
        checked += 1
        res = evaluate(fr, strategy)
        if res["unknown"]:
            unknown.append(sym)
        if not res["match"]:
            continue
        closes = fr["daily"]["Close"]
        close = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else None
        m = (member_map or {}).get(sym, {})
        matches.append({"sym": sym, "close": round(close, 2),
                        "pct": round((close / prev - 1) * 100, 2) if prev else None,
                        "indices": m.get("indices", []), "sector": m.get("sector"),
                        "values": res["detail"]})
    return {"strategy": strategy, "matches": matches, "checked": checked,
            "not_enough_history": unknown, "skipped": skipped}


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------
def _i(name, tf="daily", offset=0, **params):
    return {"type": "indicator", "name": name, "tf": tf, "offset": offset, "params": params}


def _v(value):
    return {"type": "value", "value": value}


def _scaled(o, mult=1.0, add=0.0):
    return dict(o, mult=mult, add=add)


PRESETS = [clean(s) for s in (
    {"name": "Momentum: strong and above trend",
     "conditions": [{"lhs": _i("rsi", period=14), "op": ">", "rhs": _v(60)},
                    {"lhs": _i("close"), "op": ">", "rhs": _i("ema", period=50)},
                    {"lhs": _i("volume"), "op": ">", "rhs": _i("volume_sma", period=20)}]},
    {"name": "Oversold, with volume rising",
     "conditions": [{"lhs": _i("rsi", period=14), "op": "<", "rhs": _v(30)},
                    {"lhs": _i("volume"), "op": ">", "rhs": _i("volume", offset=5)}]},
    {"name": "Pullback in an uptrend",
     "conditions": [{"lhs": _i("close"), "op": ">", "rhs": _i("ema", period=200)},
                    {"lhs": _i("rsi", period=14), "op": "<", "rhs": _v(40)},
                    {"lhs": _i("adx", period=14), "op": ">", "rhs": _v(20)}]},
    {"name": "Volume surge: 1.5x the 20-day average",
     "conditions": [{"lhs": _i("volume"), "op": ">", "rhs": _scaled(_i("volume_sma", period=20), 1.5)},
                    {"lhs": _i("close"), "op": ">", "rhs": _i("close", offset=1)}]},
    {"name": "MACD crossing up, above the 50-day",
     "conditions": [{"lhs": _i("macd", fast=12, slow=26, signal=9), "op": "crosses above",
                     "rhs": _i("macd_signal", fast=12, slow=26, signal=9)},
                    {"lhs": _i("close"), "op": ">", "rhs": _i("ema", period=50)}]},
    {"name": "Up on every timeframe",
     "conditions": [{"lhs": _i("close"), "op": ">", "rhs": _i("wma", period=20)},
                    {"lhs": _i("close", tf="weekly"), "op": ">", "rhs": _i("wma", tf="weekly", period=10)},
                    {"lhs": _i("close", tf="monthly"), "op": ">", "rhs": _i("wma", tf="monthly", period=6)}]},
    {"name": "Breaking above the Bollinger Band",
     "conditions": [{"lhs": _i("close"), "op": "crosses above", "rhs": _i("bb_high", period=20, stdev=2)}]},
)]


# ---------------------------------------------------------------------------
# your saved screens
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _saved_path(email):
    log = trade_log.user_log_path(email, "nse_index")
    if os.sep + "users" + os.sep not in log:
        raise RuntimeError("no private folder for this account")
    return os.path.join(os.path.dirname(log), "screens.json")


def saved(email):
    try:
        with open(_saved_path(email)) as fh:
            data = json.load(fh)
        return [clean(s) for s in data if isinstance(s, dict)]
    except (OSError, ValueError):
        return []


def save(email, strategy):
    s = clean(strategy)
    with _lock:
        items = [x for x in saved(email) if x["name"].lower() != s["name"].lower()]
        if len(items) >= MAX_SAVED:
            raise ValueError(f"You can keep up to {MAX_SAVED} saved screens - delete one first.")
        items.append(s)
        path = _saved_path(email)
        with open(path + ".tmp", "w") as fh:
            json.dump(items, fh)
        os.replace(path + ".tmp", path)
    return s


def delete(email, name):
    with _lock:
        items = saved(email)
        kept = [x for x in items if x["name"].lower() != (name or "").strip().lower()]
        if len(kept) == len(items):
            raise ValueError("That saved screen was not found.")
        path = _saved_path(email)
        with open(path + ".tmp", "w") as fh:
            json.dump(kept, fh)
        os.replace(path + ".tmp", path)
    return True
