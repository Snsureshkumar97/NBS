"""
big_candle.py — the day's first big 15-minute candle, as one of the account's own tickets
================================================================================
The user, 26 Oct 2026, after a day the tool sat out (Nifty +175 points in one 15-minute candle, then sideways): "can
we test entering on the first big candle breakout"; after big_candle_study.py / big_candle_robustness.py it ran on paper
for one evening; after big_candle_6y_study.py (six years, Aug 2020 - Oct 2026): "add the big candle in our rules with
live orders". Added to the rules it held over six years on Nifty, Bank Nifty and Sensex - better in 9 / 12-13 / 10 of
13 half-years - and in the one-slot form live can run (whichever comes first holds the index; an open big-candle ticket
blocks the rules' next entry until it closes) it did better still, with a deeper worst drop in the last 14 months.

THE RULE - exactly the one measured ("K1.5 end 2R"), 15-minute candles:
  a BIG candle: its body (|close - open|) at least config.BIG_CANDLE["k"] (1.5) x the 15-minute ATR(14) as it stood at
  the candle before; a BREAKOUT: it also closes beyond the day's high (green) or low (red) made before it
  the day's first such candle, from the 09:45 close (the day's first candle is never used) to the 14:30 close - a call
  on a green one, a put on a red one, at its close; ONE a day, taken or not
  stop at the candle's OTHER end (a green candle's low, a red one's high); ONE target at twice the risk; nothing moves
  the stop (a plain-exit ticket - tickets.rules_exit); out at the day's close
It is the rules' book's own ticket (TicketBook.consider_big_candle): it takes the index's slot only when nothing is open,
live orders follow it exactly as they follow any ticket on that index, and it is kept out of the rules' own cooldown,
spacing and re-arm. Switched on per index in Trade settings ("Big candle", off by default - TicketBook.big_candle_for).
"""
import datetime as dt

import pandas as pd

import config
import indicators as ind
import tickets
import trade_log
import trend_rider

BAR = pd.Timedelta(minutes=15)
ENTRY_WINDOW_S = trend_rider.ENTRY_WINDOW_S
COLS = ["Open", "High", "Low", "Close"]
_LAST_READY = {}


def _p(key):
    return (getattr(config, "BIG_CANDLE", {}) or {})[key]


def _time(hm):
    return dt.time(*hm)


def first_big(day, atr_before):
    """The day's first big candle that breaks the day's range so far, among the closes from FIRST to LAST - the day's
    first candle is never looked at - as (candle start, side +1 / -1, stop), or None. `atr_before` is ATR(14) at the
    candle BEFORE each of `day`'s candles, aligned to them. A first big breakout whose stop is not on the right side
    ends the day with no trade, as one a day means."""
    k, first, last = _p("k"), _time(_p("first_close")), _time(_p("last_close"))
    o, h, l, c = (day[x].to_numpy(float) for x in COLS)
    a = atr_before.to_numpy(float)
    for n in range(1, len(day)):
        t = day.index[n]
        close_at = (t + BAR).time()
        if close_at > last:
            break
        if close_at < first or not (a[n] == a[n]) or a[n] <= 0 or abs(c[n] - o[n]) < k * a[n]:
            continue
        side = 1 if c[n] > o[n] else -1
        if (side > 0 and c[n] <= h[:n].max()) or (side < 0 and c[n] >= l[:n].min()):
            continue
        stop = float(l[n] if side > 0 else h[n])
        if (c[n] - stop) * side <= 0:
            return None
        return t, side, stop
    return None


def _atr_before(df):
    return ind.atr(df[COLS], 14).shift(1)


def signals(df):
    """Every entry over a frame of CLOSED 15-minute candles: [(candle start, side, stop)] - the same rule as
    evaluate(), for the parity test against big_candle_study."""
    ab = _atr_before(df)
    out = []
    for _, g in df.groupby(df.index.date):
        fb = first_big(g, ab.loc[g.index])
        if fb:
            out.append(fb)
    return out


def evaluate(index_key, df, now=None):
    """The reading on the last CLOSED candle of `df` (the last row is the forming candle): {ready, side (+1/-1/0),
    stop, close, bar_close, fresh, values, why}. A candle counts as closed only when `df` already holds the next one."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if df is None or len(df) < 20:
        return {"ready": False, "why": "no candles yet"}
    if df.index[-1] + BAR <= now:
        prev = _LAST_READY.get(index_key)
        why = "waiting for the new 15-minute candle's first prices"
        return dict(prev, ready=False, why=why, side=0, fresh=False) if prev else {"ready": False, "why": why}
    closed = df.iloc[:-1][COLS]
    last = closed.index[-1]
    day = last.date()
    today = closed[closed.index.date == day]
    ab = _atr_before(closed)
    fb = first_big(today, ab.loc[today.index])
    close = float(closed["Close"].iloc[-1])
    bar_close = last + BAR
    atr_now = float(ab.iloc[-1]) if ab.iloc[-1] == ab.iloc[-1] else None
    body = abs(close - float(closed["Open"].iloc[-1]))
    side, stop, why = 0, None, None
    if fb and fb[0] == last:
        side, stop = fb[1], fb[2]
    elif fb:
        why = (f"Today's big candle was the {(fb[0] + BAR).strftime('%H:%M')} close "
               f"({'a green one - a call' if fb[1] > 0 else 'a red one - a put'}); one a day.")
    elif bar_close.time() < _time(_p("first_close")):
        why = "The day's first candle is not used - the first decision is at the 09:45 close."
    elif bar_close.time() > _time(_p("last_close")):
        why = "No big candle today: none broke the day's range by 14:30, the last close it takes."
    else:
        need = f"{_p('k'):g} x ATR {atr_now:,.1f} = {_p('k') * atr_now:,.1f}" if atr_now else "a big body"
        why = f"No big candle yet: this one's body is {body:,.1f} points, a big one needs {need} and a new day high or low."
    out = {"ready": True, "side": side, "stop": stop, "close": close, "bar_close": bar_close.isoformat(),
           "values": {"close": close, "body": round(body, 2), "atr": round(atr_now, 2) if atr_now else None,
                      "first": ({"at": (fb[0] + BAR).strftime("%H:%M"), "side": fb[1]} if fb else None)},
           "why": why, "fresh": (now - pd.Timestamp(bar_close)).total_seconds() <= ENTRY_WINDOW_S}
    _LAST_READY[index_key] = out
    return out


def apply(rec, ev, index_key, avoid_strikes=None):
    """A COPY of the engine's recommendation with the big candle's reading written in - on the day's first big candle
    the side, the strike, its premium, the stop (the candle's other end) and the one target at twice the risk that its
    ticket freezes (trend_rider.place). The engine's own recommendation is never touched."""
    rec = dict(rec)
    v = (ev or {}).get("values") or {}
    info = {"label": "Big candle", "system": "big_candle", "ready": bool((ev or {}).get("ready")),
            "why": (ev or {}).get("why"), "side": 0, "bar_close": (ev or {}).get("bar_close"),
            "fresh": bool((ev or {}).get("fresh")), "values": v, "votes": [], "filters": [], "no_cooldown": True,
            "target_r": _p("target_r"), "swing": None, "stop_atr": None,
            "tag": "Big candle" + (f" · ATR {v['atr']:,.0f}" if v.get("atr") else ""),
            "note": ("Big candle: the day's first 15-minute candle whose body is at least 1.5 x the ATR and that closes at "
                     "a new day high (a call) or low (a put); stop at the candle's other end, one target at twice the "
                     "risk, one a day - only when nothing else is open on the index.")}
    return trend_rider.place(rec, info, ev, {"target_r": _p("target_r")}, avoid_strikes,
                             "the day's first big 15-minute candle",
                             lambda side, spot, stop: _why_not(ev, side, spot, stop))


def _why_not(ev, side, spot, stop):
    if not (ev or {}).get("ready"):
        return [f"Waiting - {(ev or {}).get('why') or 'no reading yet'}."]
    if side and (spot is None or stop is None):
        return ["No price to measure the stop from yet."]
    if side:
        return [f"The big candle closed, but price has already gone past its stop ({stop:,.2f}) - no trade."]
    return [(ev or {}).get("why") or "No big candle yet."]


def summary(readings, book):
    """For the home pane: each index's switch and today's reading, its open big-candle ticket, and every big-candle
    ticket's record so far - read from the account's own log (real fills where there were real orders)."""
    try:
        rows = [r for r in trade_log._read_rows(book.path) if r.get("event") == "CLOSE" and r.get("system") == "big_candle"]
    except Exception:
        rows = []
    today = tickets.now_ist().strftime("%Y-%m-%d")
    done = [r for r in rows if r.get("pnl") not in (None, "")]

    def tally(sel):
        pnl = [float(r["pnl"]) for r in sel]
        return {"closed": len(pnl), "wins": sum(1 for p in pnl if p > 0), "net": round(sum(pnl), 2)}
    by = {}
    for name in config.instruments_in(book.market):
        pub = book.public(name)
        tk = pub.get("ticket")
        by[name] = dict(readings.get(name) or {}, on=book.big_candle_for(name) == "on",
                        ticket=tk if tk and tk.get("system") == "big_candle" else None)
    return {"by_index": by, "record": tally(done), "today": tally([r for r in done if r.get("date") == today])}
