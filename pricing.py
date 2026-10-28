"""
pricing.py — the studies' option price, corrected to what real option prices do
================================================================================
Black-Scholes on the day's sigma (yesterday's VIX scaled to the index, regime_study.features) and calendar time to
15:30 on expiry day - with that sigma multiplied by SIGMA_RATIO, the real at-the-money implied volatility over the
model's sigma, measured by pricing_calibration.py from the recorder's real option prices, by days to expiry and time
of day. Without it the model was -296 a trade too rosy on the TradingView rating's backtest trades (live_vs_backtest.py,
28 Oct 2026), -1,029 a trade under a day from expiry: real premiums decay faster than calendar-time Black-Scholes says.
"""
DTE_EDGES = (0, 0.3, 1, 2, 3, 5, 7, 14, 400)          # days to 15:30 on expiry day
TOD_EDGES = (0, 60, 180, 300, 376)                    # minutes since 09:15: first hour, late morning, midday, last hour
# {(dte cell, tod cell): real implied volatility / the model's sigma}: pricing_calibration.py, 28 Oct 2026, every recorded
# day 10 Sep - 26 Oct (14,000+ at-the-money readings). Held out (learned to 9 Oct, judged on 71 trades after): the
# model's gap to real prices -376 a trade -> -165; over all 165 trades -296 -> -69. Cell (1, *) - 0.3 to 1 day - is
# the overnight before expiry day: never priced. Empty this and set TIME = ("calendar",) for the studies' old model.
SIGMA_RATIO = {(0, 0): 0.931, (0, 1): 0.957, (0, 2): 1.074, (0, 3): 1.488, (1, 0): 1.0, (1, 1): 1.0, (1, 2): 1.0,
               (1, 3): 1.0, (2, 0): 0.943, (2, 1): 0.96, (2, 2): 0.998, (2, 3): 1.001, (3, 0): 0.851, (3, 1): 0.894,
               (3, 2): 0.898, (3, 3): 0.903, (4, 0): 0.922, (4, 1): 0.944, (4, 2): 0.956, (4, 3): 0.96, (5, 0): 0.943,
               (5, 1): 0.931, (5, 2): 0.946, (5, 3): 0.951, (6, 0): 0.909, (6, 1): 0.916, (6, 2): 0.927, (6, 3): 0.916,
               (7, 0): 0.872, (7, 1): 0.894, (7, 2): 0.914, (7, 3): 0.919}
# The clock the option decays on. ("calendar",): every hour alike, nights and weekends included (the studies until 28
# Oct 2026). ("trading", w): the session's 375 minutes, and each night (weekend nights too) as w of a session - real
# premiums lose most of their time value while the market is open. pricing_calibration.py picks w.
TIME = ("trading", 0.2)        # picked by pricing_calibration.py on the learning weeks (every clock tried was within
SESSION = 375                   # a few rupees a trade of the others - the correction is the table, not the clock)
# Which expiry a study's trade buys (pro_study.trade_expiry): the first with at least this many calendar days to run.
# 0 = the nearest, as the live tool buys today. expiry_study.py (28 Oct 2026) measures further ones.
MIN_DTE = 0


def years(expiry, when, time=None):
    """Years to 15:30 on the expiry DATE on this clock (with the day's sigma, an annual volatility)."""
    import datetime as dt
    import pro_study as ps
    mode = TIME if time is None else time
    if mode[0] == "calendar":
        return ps.years_to(expiry, when)
    w = mode[1]
    close = when.replace(hour=15, minute=30, second=0, microsecond=0)
    mins = max((close - when).total_seconds() / 60, 0.0)
    d = when.date()
    while d < expiry:
        d += dt.timedelta(days=1)
        mins += w * SESSION + (SESSION if d.weekday() < 5 else 0)
    return max(mins / (252 * SESSION + 365 * w * SESSION), 1e-6)


def _cell(x, edges):
    for n in range(len(edges) - 1):
        if edges[n] <= x < edges[n + 1]:
            return n
    return len(edges) - 2 if x >= edges[-1] else 0


def ratio(dte, tod, table=None):
    t = SIGMA_RATIO if table is None else table
    return t.get((_cell(dte, DTE_EDGES), _cell(tod, TOD_EDGES)), 1.0)


def premium(S, K, expiry, when, sigma, call, table=None, time=None):
    """The option's price at `when` (a tz-aware time) for an expiry DATE: Black-Scholes on this clock and the
    corrected sigma. The correction's cells are read in calendar days to expiry, whatever the clock."""
    import pro_study as ps
    import regime_study as rs
    dte = ps.years_to(expiry, when) * 365
    return rs.bs(S, K, years(expiry, when, time), sigma * ratio(dte, when.hour * 60 + when.minute - 555, table), call)
