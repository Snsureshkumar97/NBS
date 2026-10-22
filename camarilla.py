"""
camarilla.py — the Camarilla H4/L4 breakout as a live system on Sensex
================================================================================
The user, 22 Oct 2026, after indian_strategies_study.py and indian_strategies_robustness.py: "yes build camarilla for
sensex". Of eight popular Indian intraday strategies it was one of two that beat the tool's own rules on Sensex and held
up: per lot after costs, +215,988 / +75,447 (first two years / held-out) against the rules' +78,098 / +55,603, better
in 5 of 7 half-years, still ahead with double slippage; its Monte Carlo 95% worst drop is deeper (56,483 against
39,783 held-out). Offered on Sensex only (config.SYSTEM_INDICES); chosen per index in Trade settings.

THE RULES - exactly those measured (indian_strategies_study.run, "CAMARILLA", 15-minute candles):
  the levels, from the PREVIOUS session's high H, low L and close C (its 15-minute candles):
      H4 = C + 1.1 x (H - L) / 2      L4 = C - 1.1 x (H - L) / 2      H3 = C + 1.1 x (H - L) / 4      L3 = C - 1.1 x (H - L) / 4
  the day's FIRST 15-minute close above H4 -> buy CE, below L4 -> buy PE, looked for on the closes from the day's second
  candle (09:45) to 14:30; ONE a day: after the first close beyond either level nothing more that day, taken or not
  stop at H3 (a call) / L3 (a put); ONE target at twice the risk; no step-up, trail, 2-hour breakeven or reversal exit
  (a plain-exit ticket - tickets.rules_exit); out at the day's close, as every system's ticket
It reaches the ticket book through the entry-rule path (tickets._consider_rule) like the Trend Rider and the TradingView
rating: one decision per closed candle, in the first ENTRY_WINDOW_S after it; trend_rider.place freezes the levels.
"""
import datetime as dt

import pandas as pd

import cpr
import trend_rider

BAR = pd.Timedelta(minutes=15)
ENTRY_WINDOW_S = trend_rider.ENTRY_WINDOW_S
TARGET_R = 2.0
FIRST_CLOSE, LAST_CLOSE = dt.time(9, 45), dt.time(14, 30)
COLS = ["Open", "High", "Low", "Close"]
_LAST_READY = {}


def levels(h, l, c):
    """H4, L4, H3, L3 from one session's high, low and close."""
    rg = float(h) - float(l)
    c = float(c)
    return {"h4": c + 1.1 * rg / 2, "l4": c - 1.1 * rg / 2, "h3": c + 1.1 * rg / 4, "l3": c - 1.1 * rg / 4}


def first_breakout(day_candles, lv):
    """The day's first 15-minute close beyond H4 or L4 among the closes from FIRST_CLOSE to LAST_CLOSE - the day's
    first candle is never looked at - as (candle start, side +1 / -1), or None."""
    for n, (t, row) in enumerate(day_candles.iterrows()):
        if n == 0:
            continue
        if (t + BAR).time() > LAST_CLOSE:
            break
        c = float(row["Close"])
        if c > lv["h4"]:
            return t, 1
        if c < lv["l4"]:
            return t, -1
    return None


def signals(df):
    """Every Camarilla entry over a frame of CLOSED 15-minute candles: [(candle start, side, stop)] - the same rule as
    evaluate(), for the parity test against indian_strategies_study."""
    sess = cpr.sessions_from(df)
    by_day = {d: n for n, (d, *_x) in enumerate(sess)}
    out = []
    for d, g in df.groupby(df.index.date):
        n = by_day.get(d)
        if not n:
            continue
        _, h, l, c = sess[n - 1]
        lv = levels(h, l, c)
        fb = first_breakout(g, lv)
        if fb:
            out.append((fb[0], fb[1], lv["h3"] if fb[1] > 0 else lv["l3"]))
    return out


def evaluate(index_key, df, now=None):
    """The reading on the last CLOSED candle of `df` (the last row is the forming candle): {ready, side (+1/-1/0),
    stop, close, bar_close, fresh, values, why}. A candle counts as closed only when `df` already holds the next one."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if df is None or len(df) < 3:
        return {"ready": False, "why": "no candles yet"}
    if df.index[-1] + BAR <= now:
        prev = _LAST_READY.get(index_key)
        why = "waiting for the new 15-minute candle's first prices"
        # after the session (or on a closed day): the next session's levels are already known - said, so they can be
        # seen the evening before
        ist = now.tz_convert("Asia/Kolkata") if now.tzinfo else now.tz_localize("Asia/Kolkata")
        if df.index[-1].date() < ist.date() or ist.time() >= dt.time(15, 30):
            sess = cpr.sessions_from(df[COLS])
            if sess:
                nx = levels(*sess[-1][1:])
                why += (f" - the next session's levels: H4 {nx['h4']:,.2f} / L4 {nx['l4']:,.2f} (stops H3 "
                        f"{nx['h3']:,.2f} / L3 {nx['l3']:,.2f}), from {sess[-1][0]}'s high, low and close")
        return dict(prev, ready=False, why=why, side=0, fresh=False) if prev else {"ready": False, "why": why}
    closed = df.iloc[:-1][COLS]
    last = closed.index[-1]
    day = last.date()
    sess = cpr.sessions_from(closed, before=day)
    if not sess:
        return {"ready": False, "why": "no earlier session in the candles to set the levels from"}
    pd_, h, l, c = sess[-1]
    lv = levels(h, l, c)
    today = closed[closed.index.date == day]
    fb = first_breakout(today, lv)
    close = float(closed["Close"].iloc[-1])
    bar_close = last + BAR
    side, stop, why = 0, None, None
    if fb and fb[0] == last:
        side = fb[1]
        stop = lv["h3"] if side > 0 else lv["l3"]
    elif fb:
        why = (f"Today's one Camarilla trade was the {(fb[0] + BAR).strftime('%H:%M')} close "
               f"({'above H4 - a call' if fb[1] > 0 else 'below L4 - a put'}); there is no second one.")
    elif bar_close.time() < FIRST_CLOSE:
        why = "The day's first candle is not used - the first decision is at the 09:45 close."
    elif bar_close.time() > LAST_CLOSE:
        why = "No Camarilla trade today: no close went beyond H4 or L4 by 14:30, the last one it takes."
    else:
        why = (f"The close ({close:,.2f}) is between L4 ({lv['l4']:,.2f}) and H4 ({lv['h4']:,.2f}) - a trade needs a "
               "close beyond one of them.")
    values = {"close": close, **{k: round(v, 2) for k, v in lv.items()},
              "prev": {"session": str(pd_), "high": round(float(h), 2), "low": round(float(l), 2), "close": round(float(c), 2)},
              "first": ({"at": (fb[0] + BAR).strftime("%H:%M"), "side": fb[1]} if fb else None)}
    out = {"ready": True, "side": side, "stop": stop, "close": close, "bar_close": bar_close.isoformat(),
           "values": values, "why": why,
           "fresh": (now - pd.Timestamp(bar_close)).total_seconds() <= ENTRY_WINDOW_S}
    _LAST_READY[index_key] = out
    return out


def _conditions(ev):
    """The card's rows: H4 and L4 against the close, the one-a-day rule and the time window."""
    v = (ev or {}).get("values") or {}
    if not (ev or {}).get("ready") or v.get("h4") is None:
        return [], 0
    c, h4, l4 = v["close"], v["h4"], v["l4"]
    lean = 1 if c - h4 >= l4 - c else -1
    first = v.get("first")
    bar = pd.Timestamp((ev or {}).get("bar_close")) if (ev or {}).get("bar_close") else None
    in_window = bar is not None and FIRST_CLOSE <= bar.time() <= LAST_CLOSE
    p = v.get("prev") or {}
    return [
        ("cm_h4", "Close above H4", c > h4, f"{c:,.2f} vs H4 {h4:,.2f}",
         (f"The 15-minute close {c:,.2f} is above H4 ({h4:,.2f}) - a call's condition.",
          f"The 15-minute close {c:,.2f} is not above H4 ({h4:,.2f}).")),
        ("cm_l4", "Close below L4", c < l4, f"{c:,.2f} vs L4 {l4:,.2f}",
         (f"The 15-minute close {c:,.2f} is below L4 ({l4:,.2f}) - a put's condition.",
          f"The 15-minute close {c:,.2f} is not below L4 ({l4:,.2f}).")),
        ("cm_first", "First breakout today", not first or (bar is not None and first["at"] == bar.strftime("%H:%M")),
         ("none yet" if not first else f"{first['at']} ({'call' if first['side'] > 0 else 'put'})"),
         ("One trade a day: only the day's first close beyond H4 or L4 counts.",
          f"Today's first breakout was the {first['at'] if first else ''} close - one a day.")),
        ("cm_window", "Between 09:45 and 14:30", in_window, bar.strftime("%H:%M") if bar is not None else "—",
         ("The close is inside the window the rule was tested on (09:45-14:30).",
          "Outside 09:45-14:30 - the day's first candle is not used, and nothing after the 14:30 close.")),
        ("cm_levels", "Levels from the last session", True,
         f"H {p.get('high', 0):,.0f} · L {p.get('low', 0):,.0f} · C {p.get('close', 0):,.0f}",
         (f"H4 / L4 / H3 / L3 are worked from {p.get('session')}'s high, low and close.",
          "")),
    ], lean


def apply(rec, ev, index_key, avoid_strikes=None):
    """Write the reading into the engine's recommendation for this index - the card's rows, and on the day's first close
    beyond H4 / L4 the side, the strike, its premium, the stop (H3 / L3) and the one target at twice the risk that a
    ticket freezes (trend_rider.place). Anything incomplete is no trade, said in words."""
    rows, lean = _conditions(ev)
    v = (ev or {}).get("values") or {}
    info = {"label": "Camarilla breakout", "system": "camarilla", "ready": bool((ev or {}).get("ready")),
            "why": (ev or {}).get("why"), "side": 0, "bar_close": (ev or {}).get("bar_close"),
            "fresh": bool((ev or {}).get("fresh")), "values": v, "votes": [], "no_cooldown": True,
            "filters": [{"key": k, "name": n, "ok": ok, "reading": rd, "texts": t} for k, n, ok, rd, t in rows],
            "target_r": TARGET_R, "swing": None, "stop_atr": None,
            "tag": "Camarilla · " + (f"H4 {v['h4']:,.0f} / L4 {v['l4']:,.0f}" if v.get("h4") is not None else "no levels"),
            "note": ("Camarilla breakout: the levels come from the last session's high, low and close - H4 / L4 = close +/- "
                     "1.1 x the range / 2, H3 / L3 = close +/- 1.1 x the range / 4. A call on the day's first 15-minute "
                     "close above H4, a put on the first below L4 (closes from 09:45 to 14:30); one trade a day. Stop at "
                     "H3 / L3, one target at twice the risk, out at the day's close - nothing moves the stop, as tested.")}
    return trend_rider.place(rec, info, ev, {"target_r": TARGET_R}, avoid_strikes,
                             "the day's first 15-minute close beyond the Camarilla " + ("H4" if (ev or {}).get("side", 0) > 0 else "L4"),
                             lambda side, spot, stop: _why_not(ev, side, spot, stop))


def _why_not(ev, side, spot, stop):
    if not (ev or {}).get("ready"):
        return [f"Waiting - {(ev or {}).get('why') or 'no reading yet'}."]
    if side and (spot is None or stop is None):
        return ["No price to measure the stop from yet."]
    if side:
        return [f"The close broke out, but price has already gone past its stop ({stop:,.2f}) - no trade."]
    return [(ev or {}).get("why") or "No close beyond H4 or L4 yet."]
