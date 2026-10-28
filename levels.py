"""
levels.py — yesterday's support and resistance: its high, its low and its classic floor pivots
================================================================================
sr_study.py (28 Oct 2026, the honest backtest): skipping a TradingView-rating entry whose next such level lies closer
than 1.5x the trade's risk took it from -69 to +46 a trade over five years and +33 to +193 the year after - better
trades, not only fewer. Used live by tickets._levels_hold (config.LEVELS_CHOICES, per account and index) and by the
study itself, so both read the same levels.
"""
NAMES = {"H": "yesterday's high", "L": "yesterday's low", "P": "the pivot", "R1": "R1", "S1": "S1", "R2": "R2",
         "S2": "S2"}


def pivots(H, L, C):
    """{name: level} from one day's high, low and close."""
    P = (H + L + C) / 3
    return {"H": H, "L": L, "P": P, "R1": 2 * P - L, "S1": 2 * P - H, "R2": P + (H - L), "S2": P - (H - L)}


def day_levels(df):
    """{date: sorted levels from the trading day before it} for every day in `df` (candles with a DatetimeIndex)."""
    return {d: sorted(set(v.values())) for d, v in named_levels(df).items()}


def named_levels(df):
    """{date: {name: level}} from the trading day before each day in `df`."""
    d = df.groupby(df.index.date).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    out, prev = {}, None
    for day, row in d.iterrows():
        if prev is not None:
            out[day] = pivots(*prev)
        prev = (float(row.H), float(row.L), float(row.C))
    return out


def next_level(levels, price, up):
    """The nearest level strictly beyond `price` in that direction, or None."""
    if up:
        above = [x for x in levels if x > price]
        return min(above) if above else None
    below = [x for x in levels if x < price]
    return max(below) if below else None


def blocking(df, price, stop, up, room_r):
    """The level that stands too close in the trade's way - (name, level, distance, risk) - or None: the nearest of
    yesterday's levels beyond `price` toward the target, closer than room_r x the risk (|price - stop|)."""
    if df is None or len(df) == 0 or price is None or stop is None:
        return None
    risk = abs(price - stop)
    if risk <= 0:
        return None
    today = df.index[-1].date()
    named = named_levels(df).get(today)
    if not named:
        return None
    nxt = next_level(list(named.values()), price, up)
    if nxt is None or abs(nxt - price) >= room_r * risk:
        return None
    name = next(n for n, v in named.items() if v == nxt)
    return NAMES[name], nxt, abs(nxt - price), risk
