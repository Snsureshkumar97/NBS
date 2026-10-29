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
# {(dte cell, tod cell): real implied volatility / the model's sigma}: pricing_calibration.py, every recorded day 10
# Sep - 26 Oct (14,000+ at-the-money readings). 29 Oct 2026, with its clock_gains guard: far_expiry_check.py found the
# 28 Oct table's 14+ day row rising through the day (0.872 -> 0.919) faster than a far option decays, so a far option
# GAINED from the clock alone (+1,009 a lot, 09:45 -> 14:45, the index unchanged) and real Bank Nifty far trades were
# +296 a trade rosier in the model than real; a row that lets an option held 2+ hours gain over 1% from the clock now
# takes one ratio all day. Re-learned so, the rule picked the calendar clock (learning trades +37 a trade; the trading
# clocks +64 to +133). Held out (learned to 9 Oct, judged on 71 trades after): -376 a trade -> -160 (the 28 Oct
# table -165); over all 165 trades -296 -> -49. Cell (1, *) - 0.3 to 1 day - is the overnight before expiry day:
# never priced. Empty this for the studies' old model (calendar clock, no correction).
SIGMA_RATIO = {(0, 0): 1.933, (0, 1): 1.987, (0, 2): 2.23, (0, 3): 3.09, (1, 0): 1.0, (1, 1): 1.0, (1, 2): 1.0,
               (1, 3): 1.0, (2, 0): 1.31, (2, 1): 1.291, (2, 2): 1.273, (2, 3): 1.193, (3, 0): 1.099, (3, 1): 1.13,
               (3, 2): 1.094, (3, 3): 1.067, (4, 0): 1.031, (4, 1): 1.019, (4, 2): 0.982, (4, 3): 0.978, (5, 0): 0.977,
               (5, 1): 0.952, (5, 2): 0.942, (5, 3): 0.931, (6, 0): 0.956, (6, 1): 0.949, (6, 2): 0.951, (6, 3): 0.929,
               (7, 0): 0.92, (7, 1): 0.92, (7, 2): 0.92, (7, 3): 0.92}
# The clock the option decays on. ("calendar",): every hour alike, nights and weekends included. ("trading", w): the
# session's 375 minutes, and each night (weekend nights too) as w of a session. pricing_calibration.py picks it: the
# trading clock (w 0.2) from 28 Oct, the calendar clock from 29 Oct (held out the two are alike, -160 against -165).
TIME = ("calendar",)
SESSION = 375                   # the trading clock's session, in minutes
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
