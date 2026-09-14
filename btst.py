"""btst.py - Buy Today, Sell Tomorrow on the Indian indices: the rules, in one place.

A BTST trade buys an index option into the close and sells it at the next
morning's open. The same functions here decide a signal in the backtest
(btst_study.py) and on the screen, so what the BTST section shows today is
exactly what was tested - not a cousin of it.

THE THRESHOLDS WERE SET BEFORE THE BACKTEST WAS RUN. They are ordinary,
textbook definitions of each setup, chosen for being plain rather than for
fitting three years of history. Tuning them on the backtest's own results
would turn it into a description of the past; if they change, the study has
to be re-run and judged on the held-out year again.

What an option buyer holding overnight is up against, and why most BTST
setups do not survive being priced honestly:
  * a night of time decay - three nights over a weekend
  * the overnight gap, which no stop can protect against
  * Zerodha's charges and the spread, on both legs
"""
import datetime as _dt

import pandas as pd

SESSION_LAST_BAR = (15, 15)       # the 15:15 bar closes at 15:30 - the entry
EXIT_BAR = (9, 15)                # the next session's first bar, closing 09:30
MIN_BARS = 24                     # a full session is 25 fifteen-minute bars

# ---- pre-declared thresholds (see the note above before touching these) ----
STRONG_CLOSE_POS = 0.80           # close in the top 20% of the day's range
STRONG_CLOSE_RET = 0.005          # and the day up at least 0.5%
LATE_PUSH_RET = 0.003             # the last hour (14:30-15:30) moves 0.3%
BREAKOUT_POS = 0.75               # breakout close near the day's extreme
TREND_POS = 0.70                  # trend close in the top 30% of the range
EMA_DAYS = 20

RULES = {
    "strong_close": {
        "label": "Strong close",
        "text": "The day closes in the top 20% of its range and is up 0.5% or more "
                "(CE); the mirror image for a PE."},
    "late_push": {
        "label": "Late-session push",
        "text": "The last hour, 14:30 to 15:30, moves 0.3% or more the same way "
                "the day did."},
    "breakout_close": {
        "label": "Breakout close",
        "text": "The close is beyond the previous session's high (CE) or low (PE), "
                "and near the day's extreme."},
    "trend_close": {
        "label": "Trend close",
        "text": "The close is above its 20-day average and in the top 30% of the "
                "range (CE); below it and in the bottom 30% (PE)."},
    "always_ce": {
        "label": "Every day, CE",
        "text": "No filter: buy a call into every close. The yardstick - what a "
                "night of time decay costs before any skill."},
    "always_pe": {
        "label": "Every day, PE",
        "text": "No filter: buy a put into every close."},
}


def day_table(df):
    """One row per COMPLETE session, from 15-minute bars.

    Everything a rule reads is known at the 15:30 close. The next session's
    09:15 open and 09:30 close ride along for the backtest's exit and are
    never read by a rule. Partial sessions (a special Muhurat hour, a bar
    feed that stopped early) are dropped, and "previous" means the previous
    complete session.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    idx = df.index
    mins = idx.hour * 60 + idx.minute
    dates = idx.date
    g = df.groupby(dates)
    out = pd.DataFrame({
        "open": g["Open"].first(), "high": g["High"].max(), "low": g["Low"].min(),
        "close": g["Close"].last(), "bars": g["Close"].size()})
    last_stamp = pd.Series(idx, index=idx).groupby(dates).last()
    out["entry_ts"] = last_stamp + pd.Timedelta(minutes=15)
    out["complete"] = ((out["bars"] >= MIN_BARS)
                       & last_stamp.map(lambda t: (t.hour, t.minute) == SESSION_LAST_BAR))
    c1415 = df["Close"].where(mins == 14 * 60 + 15).groupby(dates).last()
    first = df[mins == EXIT_BAR[0] * 60 + EXIT_BAR[1]]
    fo = first["Open"].groupby(first.index.date).first()
    fc = first["Close"].groupby(first.index.date).first()
    out["c1430"] = c1415
    out["o0915"] = fo
    out["c0930"] = fc

    out = out[out["complete"]].copy()
    out["prev_high"] = out["high"].shift(1)
    out["prev_low"] = out["low"].shift(1)
    out["prev_close"] = out["close"].shift(1)
    out["ret"] = out["close"] / out["prev_close"] - 1
    rng = out["high"] - out["low"]
    out["pos"] = ((out["close"] - out["low"]) / rng).where(rng > 0, 0.5)
    out["last_hour"] = out["close"] / out["c1430"] - 1
    out["ema20"] = out["close"].ewm(span=EMA_DAYS, adjust=False).mean()
    out["next_date"] = pd.Series(out.index, index=out.index).shift(-1)
    out["next_o0915"] = out["o0915"].shift(-1)
    out["next_c0930"] = out["c0930"].shift(-1)
    return out


def signal(rule, r):
    """'CE', 'PE' or None for one session row from day_table()."""
    def ok(*vals):
        return all(v is not None and not pd.isna(v) for v in vals)
    ret, pos, lh = r.get("ret"), r.get("pos"), r.get("last_hour")
    c, ph, pl, ema = r.get("close"), r.get("prev_high"), r.get("prev_low"), r.get("ema20")
    if rule == "always_ce":
        return "CE"
    if rule == "always_pe":
        return "PE"
    if rule == "strong_close" and ok(ret, pos):
        if pos >= STRONG_CLOSE_POS and ret >= STRONG_CLOSE_RET:
            return "CE"
        if pos <= 1 - STRONG_CLOSE_POS and ret <= -STRONG_CLOSE_RET:
            return "PE"
    if rule == "late_push" and ok(ret, lh):
        if lh >= LATE_PUSH_RET and ret > 0:
            return "CE"
        if lh <= -LATE_PUSH_RET and ret < 0:
            return "PE"
    if rule == "breakout_close" and ok(c, ph, pl, pos):
        if c > ph and pos >= BREAKOUT_POS:
            return "CE"
        if c < pl and pos <= 1 - BREAKOUT_POS:
            return "PE"
    if rule == "trend_close" and ok(c, ema, pos):
        if c > ema and pos >= TREND_POS:
            return "CE"
        if c < ema and pos <= 1 - TREND_POS:
            return "PE"
    return None


def session_row(df15, daily=None, now=None):
    """Today's BTST reading from live bars: the row a rule reads, and whether
    it is final.

    df15   15-minute bars covering today and at least the previous session.
    daily  daily candles (optional) for the 20-day average. Without them the
           average comes from the complete sessions inside df15, which is only
           right once there are a few months of them - so pass them live.
    now    the current time. A 15:15 bar is still forming until 15:30, and a
           live feed shows it while it forms, so bars alone cannot say whether
           today has closed.

    Bars stamped after 15:15 are ignored: some feeds add a 15:30 bar for the
    closing print, and the backtest's session ends with the 15:15 bar.

    Built with day_table() on the same bars, so a session that has closed
    produces exactly the numbers the backtest used. Before the 15:15 bar has
    closed the row is PROVISIONAL: the close, the range and the last hour are
    all still moving, and a rule that fires at 14:50 may not at 15:30.
    """
    if df15 is None or len(df15) == 0:
        return None
    mins_all = df15.index.hour * 60 + df15.index.minute
    df15 = df15[mins_all <= SESSION_LAST_BAR[0] * 60 + SESSION_LAST_BAR[1]]
    if len(df15) == 0:
        return None
    idx = df15.index
    today = idx[-1].date()
    past = day_table(df15[idx.date < today])
    if len(past) == 0:
        return None
    prev = past.iloc[-1]
    bars = df15[idx.date == today]
    mins = bars.index.hour * 60 + bars.index.minute
    last = bars.index[-1]
    final = (len(bars) >= MIN_BARS and (last.hour, last.minute) == SESSION_LAST_BAR)
    if final and now is not None and now.date() == today and (now.hour, now.minute) < (15, 30):
        final = False                    # the 15:15 bar is still forming
    close = float(bars["Close"].iloc[-1])
    high, low = float(bars["High"].max()), float(bars["Low"].min())
    rng = high - low
    c1430 = bars["Close"].where(mins == 14 * 60 + 15).dropna()

    closes = None
    if daily is not None and len(daily):
        dc = daily["Close"].copy()
        dc.index = [t.date() if hasattr(t, "date") else t for t in dc.index]
        dc = dc[[d < today for d in dc.index]]
        closes = pd.concat([dc, pd.Series([close], index=[today])])
    else:
        closes = pd.concat([past["close"], pd.Series([close], index=[today])])
    ema20 = float(closes.ewm(span=EMA_DAYS, adjust=False).mean().iloc[-1])

    row = {"date": str(today), "final": bool(final),
           "as_of": (last + pd.Timedelta(minutes=15)).strftime("%H:%M"),
           "open": float(bars["Open"].iloc[0]), "high": high, "low": low, "close": close,
           "prev_high": float(prev["high"]), "prev_low": float(prev["low"]),
           "prev_close": float(prev["close"]),
           "ret": close / float(prev["close"]) - 1,
           "pos": (close - low) / rng if rng > 0 else 0.5,
           "last_hour": (close / float(c1430.iloc[-1]) - 1) if len(c1430) else None,
           "ema20": ema20, "ema_days_used": int(len(closes))}
    row["signals"] = {rule: signal(rule, row) for rule in RULES}
    return row


# ---------------------------------------------------------------------------
# the overnight warning
# ---------------------------------------------------------------------------
# The study's one useful answer is to "should I carry this past the close?",
# so that is where it is used: on a ticket still open in the last hour.
WARN_FROM = (14, 30)
WARN_UNTIL = (15, 40)


def next_session_open(now):
    """09:15 on the next NSE trading day after `now`'s date."""
    import main
    d = now.date() + _dt.timedelta(days=1)
    while d.weekday() >= 5 or main.is_nse_holiday(d):
        d += _dt.timedelta(days=1)
    return _dt.datetime(d.year, d.month, d.day, 9, 15, tzinfo=now.tzinfo)


def overnight(now, index, side, expiry, theta_day, study=None):
    """What carrying an open index option past today's close would cost.

    theta_day  the position's time decay per calendar day, in rupees (the
               sign is ignored). Charged for every day until the next session
               opens - a weekend is three, a holiday more - at today's rate,
               which is the least it will be: decay speeds up toward expiry.
    study      btst_study.py's saved results, for what buying into the close
               actually did on this index and side in the held-out year.
    """
    import main
    today = now.date()
    trading_today = today.weekday() < 5 and not main.is_nse_holiday(today)
    show = trading_today and WARN_FROM <= (now.hour, now.minute) <= WARN_UNTIL
    nxt = next_session_open(now)
    days = (nxt - now).total_seconds() / 86400.0
    expires_today = str(expiry or "")[:10] == today.isoformat()
    decay = (round(abs(theta_day) * days)
             if theta_day is not None and not expires_today else None)
    baseline = None
    rule = "always_ce" if side == "CE" else "always_pe"
    entry = ((((study or {}).get("results") or {}).get(index) or {}).get(rule) or {}).get(side)
    if entry and entry.get("oos"):
        o = entry["oos"]
        baseline = {"avg": o["avg"], "n": o["n"], "win": o["win"]}
    return {"show": bool(show), "next_open": nxt.strftime("%a %d %b, %H:%M"),
            "days": round(days, 2), "calendar_nights": (nxt.date() - today).days,
            "expires_today": expires_today, "decay": decay, "baseline": baseline}
