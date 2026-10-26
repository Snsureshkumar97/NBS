"""
big_candle.py — the day's first big 15-minute candle, ON PAPER, beside the tool's own tickets
================================================================================
The user, 26 Oct 2026, after a day the tool sat out (Nifty +175 points in one 15-minute candle, then sideways): "can
we test entering on the first big candle breakout", then, after big_candle_study.py and big_candle_robustness.py: "yes
build it on paper first". What held up there: added to the rules where they hold nothing, on Nifty, Bank Nifty and
Sensex (walk-forward steady), with a deeper worst drop; a candidate found among the variants, so it runs on paper
first - its own book, its own log (big_candle_trades.csv), NO listener: it can never place a real order, and it never
holds an index's slot, so the tool's own tickets (and their live orders) are exactly as they were.

THE RULE - exactly the one measured (big_candle_robustness.py, "K1.5 end 2R"), 15-minute candles:
  a BIG candle: its body (|close - open|) at least config.BIG_CANDLE["k"] (1.5) x the 15-minute ATR(14) as it stood at
  the candle before; a BREAKOUT: it also closes beyond the day's high (green) or low (red) made before it
  the day's first such candle, from the 09:45 close (the day's first candle is never used) to the 14:30 close - a call
  on a green one, a put on a red one, at its close; ONE a day, taken or not
  stop at the candle's OTHER end (a green candle's low, a red one's high); ONE target at twice the risk; nothing moves
  the stop (a plain-exit ticket - tickets.rules_exit); out at the day's close
Switched on per index in Trade settings ("Big candle (paper)", off by default - TicketBook.big_candle_for); a ticket
already open is followed to its exit even if switched off.
"""
import datetime as dt
import os
import threading

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
    the side, the strike, its premium, the stop (the candle's other end) and the one target at twice the risk that a
    paper ticket freezes (trend_rider.place). The tool's own recommendation is never touched."""
    rec = dict(rec)
    v = (ev or {}).get("values") or {}
    info = {"label": "Big candle (paper)", "system": "big_candle", "ready": bool((ev or {}).get("ready")),
            "why": (ev or {}).get("why"), "side": 0, "bar_close": (ev or {}).get("bar_close"),
            "fresh": bool((ev or {}).get("fresh")), "values": v, "votes": [], "filters": [], "no_cooldown": True,
            "target_r": _p("target_r"), "swing": None, "stop_atr": None,
            "tag": "Big candle" + (f" · ATR {v['atr']:,.0f}" if v.get("atr") else ""),
            "note": ("Big candle (paper): the day's first 15-minute candle whose body is at least 1.5 x the ATR and that "
                     "closes at a new day high (a call) or low (a put); stop at the candle's other end, one target at "
                     "twice the risk, one a day. Paper only - no real order.")}
    return trend_rider.place(rec, info, ev, {"target_r": _p("target_r")}, avoid_strikes,
                             "the day's first big 15-minute candle (paper)",
                             lambda side, spot, stop: _why_not(ev, side, spot, stop))


def _why_not(ev, side, spot, stop):
    if not (ev or {}).get("ready"):
        return [f"Waiting - {(ev or {}).get('why') or 'no reading yet'}."]
    if side and (spot is None or stop is None):
        return ["No price to measure the stop from yet."]
    if side:
        return [f"The big candle closed, but price has already gone past its stop ({stop:,.2f}) - no trade."]
    return [(ev or {}).get("why") or "No big candle yet."]


class PaperDesk:
    """The big candle's paper tickets for one account: a TicketBook of its own (big_candle_trades.csv beside the
    account's trades.csv), one lot, no re-arm, and NO listener - nothing in it can reach the live orders."""

    def __init__(self, feed):
        self.feed = feed
        base = os.path.dirname(trade_log.user_log_path(feed.email, feed.market))
        self.book = tickets.TicketBook(feed.email, feed.market, path=os.path.join(base, "big_candle_trades.csv"))
        self.book.auto_rearm = False
        self.lock = threading.RLock()
        self.readings = {}
        self._tok, self._tok_tried = {}, {}

    def step(self, name, rec, on):
        """One fresh reading of the engine (Feed._live_analysis): follow the open paper ticket, and - switched on, with
        none open - take the day's first big candle in the first two minutes after its close."""
        ev = evaluate(name, (rec or {}).get("candles"), now=tickets.now_ist())
        bc = apply(rec or {}, ev, name)
        with self.lock:
            self.readings[name] = {"on": bool(on), "ready": bool(ev.get("ready")), "side": ev.get("side", 0),
                                   "why": ev.get("why"), "bar_close": ev.get("bar_close"),
                                   "first": (ev.get("values") or {}).get("first")}
        return self.book.update(name, bc) if on else self.book.track(name, bc)

    def _open_trade(self, name):
        with self.book.lock:
            b = self.book.books.get(name)
            t = b.trade if b else None
            return t if t is not None and t["status"] == "OPEN" else None

    def price_tick(self):
        """The paper ticket's own contract, off the stream, on every tick - as the AI desk's."""
        for name in list(self.book.books):
            trade = self._open_trade(name)
            if trade is None:
                continue
            px = self._stream_price(trade)
            if px is not None:
                self.book.live_price(name, px)
                self.book.tick_price(name, px)

    def _stream_price(self, trade):
        import time
        tid = trade.get("trade_id")
        handle = self._tok.get(tid)
        if handle is None:
            if time.time() - self._tok_tried.get(tid, 0) < 30:
                return None
            self._tok_tried[tid] = time.time()
            handle = self._subscribe(trade)
            if handle is None:
                return None
            self._tok[tid] = handle
        st = self.feed.streamer
        return st.price(handle) if st is not None and hasattr(st, "price") else None

    def _subscribe(self, trade):
        try:
            import user_kite
            from data_providers import KiteDataProvider
            st, token = self.feed.streamer, user_kite.token_for(self.feed.email)
            if st is None or not token or not hasattr(st, "subscribe"):
                return None
            tok = KiteDataProvider(user_kite.api_key_for(self.feed.email), token).option_token(
                trade["index"], trade["strike"], trade["option_type"], str(trade.get("expiry") or "")[:10] or None)
            if not tok:
                return None
            st.subscribe([tok])
            return tok
        except Exception:
            return None

    def public(self):
        """For the Dashboard: each index's switch and reading, its open paper ticket, today's and the whole record."""
        try:
            rows = [r for r in trade_log._read_rows(self.book.path) if r.get("event") == "CLOSE"]
        except Exception:
            rows = []
        today = tickets.now_ist().strftime("%Y-%m-%d")
        done = [r for r in rows if r.get("pnl") not in (None, "")]

        def summary(sel):
            pnl = [float(r["pnl"]) for r in sel]
            return {"closed": len(pnl), "wins": sum(1 for p in pnl if p > 0), "net": round(sum(pnl), 2)}
        with self.lock:
            readings = dict(self.readings)
        by = {}
        for name in config.instruments_in(self.feed.market):
            pub = self.book.public(name)
            by[name] = dict(readings.get(name) or {"on": False}, ticket=pub.get("ticket"))
        return {"by_index": by, "record": summary(done), "today": summary([r for r in done if r.get("date") == today]),
                "last": [{k: r.get(k) for k in ("date", "time_ist", "index", "strike", "option_type", "entry", "exit",
                                                "pnl", "status")} for r in done[-5:]][::-1]}
