"""
feeds.py — one analysis loop per connected user
================================================================================
The website used to run a single worker thread: one provider, one set of
candles, one answer, shared by everyone who loaded the page. That was the right
shape when the server held the only broker session. Now that each user brings
their own Zerodha connection, the fetch has to happen under their token, so the
loop has to be theirs too.

HOW MANY THREADS THIS ACTUALLY MEANS
    One per user *who is currently watching*, not one per account. A feed is
    started when a browser first polls for state and reaped after
    IDLE_SECONDS with nobody asking. Ten registered users who are all asleep
    cost nothing; the cost tracks the people actually looking at the page,
    which is the number that should scale it.

    That does mean N users is N times the calls to Zerodha. Each set of calls
    is against that user's own token and their own rate limit, which is the
    point — but it is also why this is not a design that reaches a thousand
    users without a shared cache in front of it. See CACHING, below.

CACHING
    There isn't any, deliberately. Nifty's candles are the same candles
    whoever asks, so a shared cache would be cheaper and would return
    identical numbers. It is left out because "identical numbers" stops being
    true the moment anything is per-user — a different expiry, a different
    index list, a lot size from their own account — and a cache that was
    correct when it was written is the kind of thing that goes quietly wrong
    later. If this ever needs to serve a crowd, add the cache then, with the
    per-user cases named explicitly.

A FEED IS NOT A SUBSCRIPTION
    Nothing here places an order, and nothing here streams ticks. Each pass is
    a plain historical-candle fetch plus the same signal_engine call the
    desktop app makes.
"""

import datetime as dt
import threading
import time
import traceback

import pandas as pd

import accounts
import config
import explain
import market_map
import signal_engine
import tickets
import user_kite
from data_providers import (DeribitDataProvider, DeribitStreamer,
                            FreeDataProvider, KiteDataProvider,
                            KiteStreamer)
from main import drop_preopen, fetch_recommendation, is_market_open, now_ist

# A feed with nobody watching it is switched off after this long. Generous
# enough to survive a user reading the explanation for a few minutes without
# the page reloading, short enough that a closed tab stops calling Zerodha.
IDLE_SECONDS = 240

# A tick socket that has delivered nothing for this long while its market is
# trading is treated as dead and rebuilt. Indices tick several times a second
# in session - and keep sending packets even through the closing auction -
# so silence this long is a broken connection, never a quiet market.
STALL_SECONDS = 45

# The option chain streamed live: this many strikes either side of the money
# (what the chain tab shows), and a tick older than this is not shown as live -
# the snapshot's own value stands instead.
CHAIN_STREAM_SPAN = 12
CHAIN_TICK_MAX_AGE = 20.0

# Stands in for a tick socket on a venue that has none, so the loops can
# tell "no stream yet" (None) apart from "this market never has one".
_NO_STREAM = object()

# In free mode nobody has a token and the data is genuinely identical for
# everyone, so every user maps to this one shared feed.
SHARED = "*free*"

# The signal only needs enough bars to warm up the slowest indicator. A chart
# you can scroll needs considerably more than that, so a deeper window is
# fetched separately and refreshed slowly — the old bars in it do not change,
# and re-pulling sixty days every thirty seconds to append one candle would be
# rude to the data provider and pointless besides.
HISTORY_DAYS = int(getattr(config, "CHART_HISTORY_DAYS", 60))
HISTORY_TTL = 240

_lock = threading.RLock()
_feeds = {}                   # key -> Feed
_stopping = threading.Event()
_settings = {"mode": "kite", "interval": 30, "expiry": None}


# How often the supervisor checks whether an always-on user needs a feed, and
# how far before the bell it starts one. The lead is so the first REST history
# fetch and the option chain are already in when the session opens, rather than
# the first minute of the day being spent loading.
RESIDENT_POLL_SECONDS = 20
RESIDENT_LEAD_MINUTES = 5

_resident = None


def _resident_window(now, market=None):
    """True when an always-on feed should be running, for one market.

    Asking is_market_open about a moment five minutes from now gets the lead
    and the weekend and holiday rules in one go, rather than reimplementing
    the calendar here and letting the two drift. Any instrument in the market
    will do - they all share the profile that answers this.
    """
    keys = config.instruments_in(market) if market else None
    k = keys[0] if keys else None
    return (is_market_open(now, k)
            or is_market_open(now + dt.timedelta(minutes=RESIDENT_LEAD_MINUTES), k))


def _resident_loop():
    """Hold a feed open through the session for users who asked for it.

    Feeds are reaped IDLE_SECONDS after the last browser poll, which means the
    tool only ever ran while somebody had the page open: close the tab at 09:16
    and nothing was analysed or logged for the rest of the day. This touches
    those users' feeds on a timer so they stay alive from just before the open
    until the close, and then stops touching them - the existing reaper takes
    them down a few minutes later with no special path to get wrong.

    Two gates, both deliberate. Outside the session nothing is started at all,
    so this is not a thread quietly calling Zerodha overnight. And a user with
    no token is skipped rather than started, because Zerodha clears tokens
    every morning and a feed without one is a loop logging failures until
    somebody signs in.
    """
    while not _stopping.wait(RESIDENT_POLL_SECONDS):
        try:
            if _settings["mode"] != "kite":
                continue
            users = accounts.always_on_users()
            if not users:
                continue
            # Per market, because their windows do not coincide. Keyed only on
            # the Indian session, an always-on crypto feed never started at
            # all - and had it started, it would have run 09:10 to 15:40 on a
            # market that has neither an open nor a close.
            for market in config.MARKETS:
                if not config.instruments_in(market):
                    continue
                if not _resident_window(now_ist(), market):
                    continue
                _resident_market(market, users)
                # Say so the first time, and only the first time. Without a
                # line in the log there is no way to tell "the supervisor
                # started the feed and the rules issued nothing" from "the
                # supervisor never ran" - and those two look identical from
                # the outside, which is exactly the question you ask when you
                # come back to an empty ticket log.
        except Exception:
            pass


def _resident_market(market, users):
    """Keep one market's feeds alive for the users who asked for it."""
    # Crypto needs no broker token; Zerodha does, and a feed without one is a
    # loop logging failures until somebody signs in.
    needs_token = config.MARKETS[market]["market_provider"] == "kite"
    for email in users:
        if needs_token and not user_kite.token_for(email):
            continue
        try:
            with _lock:
                fresh = _key_for(email, market) not in _feeds
            for_user(email, market)
            if fresh:
                # Say so the first time, and only the first time. Without this
                # line there is no telling "the supervisor ran and the rules
                # issued nothing" from "the supervisor never ran".
                print(f"[resident] {now_ist():%Y-%m-%d %H:%M:%S} feed started "
                      f"for {email} [{market}] (nobody watching)", flush=True)
        except Exception:
            continue


def configure(mode, interval, expiry=None):
    global _resident
    _settings.update(mode=mode, interval=interval, expiry=expiry)
    if _resident is None or not _resident.is_alive():
        _resident = threading.Thread(target=_resident_loop, daemon=True,
                                     name="resident")
        _resident.start()


def _key_for(email, market=None):
    """Feeds are per user AND per market.

    Two markets in one feed would share a ticket book, a session total and a
    daily limit across instruments quoted in different currencies. Keying them
    apart means the separation costs nothing to maintain: there is no shared
    state to keep straight because there is no shared feed.
    """
    market = market or config.DEFAULT_MARKET
    base = SHARED if _settings["mode"] == "free" else (email or "").strip().lower()
    return base if market == config.DEFAULT_MARKET else f"{base}#{market}"


# ---------------------------------------------------------------------------
def _ladder_odds(rec, tech):
    """Chance of touching each rung before the 15:30 bell.

    The volatility is solved from the live option chain - the same ATM implied
    volatility the Greeks tab shows - because that is what is actually on the
    record here. An earlier version of this function read three fields that do
    not exist on any recommendation (iv_used, sigma_annual, sigma); it would
    have returned blanks for ever while looking like a working feature.

    ONE HONEST GAP. touch_model's calibration factor was fitted against
    regime_study's sigma, which is yesterday's India VIX scaled by each index's
    realised volatility. This feeds it chain-derived implied volatility instead.
    The two are close today - 14.0% against a scaled VIX near 12.3 - but the
    out-of-sample validation does not strictly cover the substitution, and it
    cannot be re-run historically because the recorder holds two days of
    chains. The screen says so rather than implying the check covered this.
    """
    blank = {"t1": None, "t2": None, "t3": None, "stop": None, "minutes": 0,
             "iv": None, "horizon": None, "year_minutes": None}
    try:
        import datetime as _dt
        import greeks as gk
        import touch_model as tm

        name = rec.get("index")
        always = bool(name) and config.market_for(name)["always_open"]
        spot = rec.get("spot") or tech.get("last_close")
        chain = rec.get("option_chain") or {}
        strikes = chain.get("strikes") or []
        expiry = str(chain.get("expiry") or "")
        now = _dt.datetime.now(_dt.timezone.utc)
        try:
            exp_day = _dt.date.fromisoformat(expiry[:10])
        except ValueError:
            exp_day = None

        if always:
            # Crypto has no bell, so "before the close" was measured to 15:30
            # IST - a time that means nothing to Bitcoin. And the contract on
            # the ticket can be weeks from settling, a horizon over which every
            # level reads near certain. The next 24 hours instead, or less when
            # the contract settles sooner (Deribit, 08:00 UTC), on the calendar
            # year its implied volatility is quoted over.
            settle = (_dt.datetime(exp_day.year, exp_day.month, exp_day.day, 8, 0,
                                   tzinfo=_dt.timezone.utc) if exp_day else None)
            to_settle = (settle - now).total_seconds() / 60.0 if settle else None
            mins = 24 * 60.0 if to_settle is None else min(24 * 60.0, to_settle)
            year = tm.CALENDAR_YEAR_MINUTES
            horizon = ("in the next 24 hours" if mins >= 24 * 60.0 - 1
                       else "before this contract settles at 13:30 IST")
        else:
            ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
            settle = (_dt.datetime(exp_day.year, exp_day.month, exp_day.day, 15, 30,
                                   tzinfo=ist) if exp_day else None)
            mins = tm.minutes_to_close()
            year = tm.TRADING_YEAR_MINUTES
            horizon = "before the 15:30 close"

        base = dict(blank, minutes=round(mins) if mins and mins > 0 else 0,
                    horizon=horizon, year_minutes=year)
        if not (spot and strikes and settle) or mins <= 0:
            return base

        t_yr = gk.years_to_expiry(max(0.0, (settle - now).total_seconds() / 60.0))
        atm = min(strikes, key=lambda s_: abs(s_["strike"] - spot))
        ivs = []
        for side, kind in (("call", "CE"), ("put", "PE")):
            iv = gk.implied_vol(atm.get(f"{side}_ltp"), spot, atm["strike"], t_yr, kind)
            if iv:
                ivs.append(iv)
        if not ivs:
            return base
        sigma = sum(ivs) / len(ivs)
        out = tm.ladder_odds(spot, rec.get("index_targets"),
                             rec.get("index_stop_loss"), sigma, mins, year)
        out.update(iv=round(sigma * 100, 2), horizon=horizon, year_minutes=year)
        return out
    except Exception:
        return blank

def _trade_charges(name, entry, exits, lot_size):
    """Zerodha's charges for one round trip on ONE lot, at each exit.

    Split so the page can scale them to any number of lots exactly: brokerage
    is a flat Rs 20 an order whatever the size (plus GST on it); everything
    else - STT, exchange, SEBI, stamp and the GST on those - grows with the
    quantity. Taken from regime_study.net_rupees, the cost model the backtests
    were judged on, at zero slippage: slippage is not a charge, and the page
    says it is left out. Indian index options only; Deribit's fees are
    different and are not modelled, so crypto gets None.

    exits: [T1, T2, T3, stop] option prices.
    """
    try:
        exch = (config.INSTRUMENTS.get(name) or {}).get("kite_exchange")
        if exch not in ("NSE", "BSE") or entry is None or not lot_size:
            return None
        import regime_study as rs
        flat = 2 * rs.BROKERAGE_PER_ORDER * (1 + rs.GST)
        per = {}
        for key, px in zip(("t1", "t2", "t3", "stop"), exits):
            if px is None:
                per[key] = None
                continue
            gross = (px - entry) * lot_size
            per[key] = round(gross - rs.net_rupees(entry, px, lot_size, exch, 0.0) - flat, 2)
        return {"flat": round(flat, 2), "per_lot": per, "exchange": exch}
    except Exception:
        return None


def _live_charges(name, rec, meta):
    """Charges for the live signal - only when its premium is a real quote,
    since charges on an approximated premium would be precision about a guess."""
    if rec.get("premium_source") != "live" or rec.get("live_ltp") is None:
        return None
    pt = (list(rec.get("premium_targets") or []) + [None] * 3)[:3]
    return _trade_charges(name, rec.get("live_ltp"), pt + [rec.get("premium_stop_loss")],
                          meta.get("lot_size"))


def _ticket_odds(ticket, idx):
    """The chance of reaching an open ticket's FROZEN levels from the live index.

    The live ladder is priced against the levels the engine recalculates each
    pass; an open ticket is measured against the ones fixed at entry, so its
    chances are worked out on those - with the same implied volatility, window
    and year basis the live ladder just used. A level already reached reads
    100, because it has been.
    """
    try:
        import touch_model as tm
        o = (idx or {}).get("odds") or {}
        out = {"t1": None, "t2": None, "t3": None, "stop": None,
               "minutes": o.get("minutes") or 0, "iv": o.get("iv"),
               "horizon": o.get("horizon"), "year_minutes": o.get("year_minutes")}
        spot = (idx or {}).get("spot")
        if not (ticket and ticket.get("open") and spot and o.get("iv")
                and o.get("minutes") and o.get("year_minutes")):
            return out
        got = tm.ladder_odds(spot, ticket.get("index_targets"), ticket.get("index_stop"),
                             o["iv"] / 100.0, o["minutes"], o["year_minutes"])
        hit = ticket.get("hit") or {}
        for key in ("T1", "T2", "T3"):
            out[key.lower()] = 100 if hit.get(key) else got.get(key.lower())
        out["stop"] = 100 if ticket.get("sl_hit") else got.get("stop")
        return out
    except Exception:
        return None


def _room(rec):
    reach = rec.get("reach") or {}
    spot = rec.get("spot")
    def to(d, sign):
        return None if spot is None or d is None else round(spot + sign * d, 2)
    return {
        "up": reach.get("reach_up"), "up_to": to(reach.get("reach_up"), +1),
        "up_cap": reach.get("cap_up"),
        "down": reach.get("reach_down"), "down_to": to(reach.get("reach_down"), -1),
        "down_cap": reach.get("cap_down"),
        "side": {"CE": "up", "PE": "down"}.get(rec.get("option_type")),
    }


def _public(rec, name=None):
    """The parts of a recommendation a browser needs.

    Deliberately explicit — returning the whole dict would ship the candle
    DataFrame and the raw option chain to the browser on every poll, which is
    both slow and more than the page has any use for.
    """
    if not rec:
        return None
    meta = config.INSTRUMENTS.get(name or rec.get("index")) or {}
    tech = rec.get("technical") or {}
    trend = rec.get("trend") or {}
    return {
        "index": rec.get("index"),
        "bias": rec.get("bias"),
        "action": rec.get("action"),
        "confidence": rec.get("confidence"),
        "spot": rec.get("spot"),
        "strike": rec.get("suggested_strike"),
        "option_type": rec.get("option_type"),
        "targets": rec.get("index_targets"),
        "stop": rec.get("index_stop_loss"),
        # The premium side. The engine has computed these all along — the web
        # layer simply never forwarded them, so the site showed index points
        # only and the number a buyer actually pays was missing from it.
        "ltp": rec.get("live_ltp"),
        "spread": rec.get("spread"),
        "max_spread": getattr(config, "MAX_SPREAD_PCT", 0),
        "premium_targets": rec.get("premium_targets") or [None, None, None],
        "premium_stop": rec.get("premium_stop_loss"),
        "premium_source": rec.get("premium_source"),
        "atm_strike": rec.get("atm_strike"),
        # The contract multiplier, so the page can turn a premium move into
        # rupees without hard-coding a lot size that Zerodha revises.
        "lot_size": meta.get("lot_size"),
        "max_lots": getattr(config, "MAX_LOTS", 5),
        "expiry": (rec.get("option_chain") or {}).get("expiry"),
        # The contract dies today. On an index option that changes the trade:
        # the premium moves several times faster than the delta-0.5 ladder
        # assumes, both ways. pro_study.py found most of the backtest's profit
        # sits on these days - which is also where a constant-volatility model
        # is weakest - so it is flagged rather than hidden or banned. Not on
        # crypto, where Deribit lists a contract expiring every single day.
        "expiry_today": (not config.market_for(name or rec.get("index"))["always_open"]
                         and str((rec.get("option_chain") or {}).get("expiry") or "")[:10]
                         == now_ist().strftime("%Y-%m-%d")),
        "risk_points": rec.get("risk_points"),
        "reach_points": rec.get("reach_points"),
        # Both sides, with the price each reaches and what limits it. The rec
        # has always carried this; the page only ever saw one number, so
        # "606 pts of room" never said which way or to where.
        "room": _room(rec),
        "opening_range": rec.get("opening_range"),
        "reach_to_risk": rec.get("reach_to_risk"),
        "reach_reason": rec.get("reach_reason"),
        "not_worth_it": rec.get("not_worth_it"),
        "adx_blocked": rec.get("adx_blocked"),
        "macd_blocked": rec.get("macd_blocked"),
        # Which rung actually ends the trade. The ticket freezes this at entry;
        # before a ticket exists the page still needs it to label the ladder.
        "exit_at": (lambda v: v if v in ("T1", "T2", "T3") else "T3")(
            str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").upper()),
        "odds": _ladder_odds(rec, tech),
        # What the trade costs to do, so the page can show the reward and the
        # risk after charges rather than before them.
        "charges": _live_charges(name or rec.get("index"), rec, meta),
        "blockers": rec.get("blockers") or [],
        "votes": rec.get("votes") or {},
        "agree": rec.get("agree"), "dissent": rec.get("dissent"),
        "adx": tech.get("adx"), "adx_ok": tech.get("adx_ok"),
        "rsi": round(tech["last_rsi"], 1) if tech.get("last_rsi") is not None else None,
        "macd_hist": tech.get("macd_hist"),
        "macd_score": tech.get("macd_score"),
        "vwap_gap": tech.get("vwap_gap"),
        "trend": {
            "label": trend.get("label"), "adx": trend.get("adx"),
            "momentum": trend.get("momentum"), "direction": trend.get("direction"),
            "displacement_atr": trend.get("displacement_atr"),
            "stalled": trend.get("stalled"),
            "day_change": trend.get("day_change"), "day_change_pct": trend.get("day_change_pct"),
            "day_high": trend.get("day_high"), "day_low": trend.get("day_low"),
            "range_pos_pct": trend.get("range_pos_pct"),
        },
    }


# ---------------------------------------------------------------------------
class Feed:
    """One user's rolling analysis of all three indices."""

    def __init__(self, key, email, market=None):
        self.key = key
        self.email = email
        self.market = market or config.DEFAULT_MARKET
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.last_touch = time.time()
        self.thread = None
        self.hist = {}            # name -> DataFrame, the deep chart window
        self._ohlc_cache = {}     # (name, interval) -> (fetched_at, DataFrame)
        self._hist_cache = {}     # interval -> (fetched_at, {symbol: DataFrame}, days)
        self._hist_refreshing = set()
        self._hist_refresh_lock = threading.Lock()
        self._hist_warmed = False
        self.hist_at = 0.0
        # This user's own tickets, with their own trade log. A book per user
        # rather than one per server: the daily limits, the session total and
        # the history are all personal, and sharing them would mean one
        # person's fourth ticket capping everybody else's day.
        self.tickets = tickets.TicketBook(
            email if key != SHARED else None, market=self.market)
        self.events = []          # what just happened, newest first
        self.bell_closed = False  # whether today's tickets were squared up

        # The live price feed. Polling REST every thirty seconds can only ever
        # show a snapshot up to thirty seconds stale, and asking faster gets
        # you rate-limited. The WebSocket pushes every tick instead, which is
        # what the desktop app has always used and why its numbers moved and
        # the website's did not.
        self.streamer = None
        self.tokens = {}          # index name -> instrument token
        self.opt_tokens = {}      # index name -> the open ticket's contract
        self.opt_for = {}         # index name -> the trade_id that contract is for
        self.sug_tokens = {}      # index name -> (strike, type, token) suggested
        self.sug_px = {}          # index name -> that contract's live premium
        self.base_df = {}         # index name -> the completed REST candles
        self.base_oi = {}         # index name -> the last option-chain snapshot
        self.chain_toks = {}      # index name -> {expiry, map (strike, CE/PE) -> token, streamed, on}
        self._chain_tried = {}    # index name -> (expiry, when) of a lookup that has not succeeded
        self._last_live = 0.0     # when the live recompute last ran
        self.eq_tokens = {}       # tradingsymbol -> token, for the heat map
        self._eq_tried = False
        self._idx_tried = 0.0     # when the index tokens were last looked up
        self._stream_born = 0.0   # when the current Zerodha socket was made
        self._last_rebuild = 0.0  # when the watchdog last rebuilt it
        self._rebuild_gap = STALL_SECONDS   # backoff between rebuilds
        self._crypto = None       # built on demand, only if crypto is enabled
        self.dstream = None       # Deribit socket, for crypto feeds
        self._crypto_at = 0.0     # when the crypto price poll last ran
        self._crypto_seen = 0.0   # when it last actually got a price
        self.ticker = None        # the fast loop that reads it
        self._ticker_lock = threading.Lock()   # one tick loop per feed, however it is started
        self.live_at = 0.0        # when the live recompute last updated an index
        self.live_errors = {}     # "tick loop" or index name -> (what went wrong, when)
        self._fault_said = {}     # (where, what) -> when it was last written to the log
        self.spots = {}           # index name -> newest streamed spot
        self.stream_error = None  # why the tick socket is not up, if it is not
        self.stage = "starting"   # where the analysis loop currently is
        self.state = {
            "indices": {},
            "market_open": False,
            "updated": None,
            "feed": "starting",
            "error": None,
            "started": now_ist().isoformat(),
        }

    # -- lifecycle ----------------------------------------------------------
    def touch(self):
        self.last_touch = time.time()
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True,
                                           name=f"feed:{self.key}")
            self.thread.start()
            self.warm_history()
        # And the tick loop, the same way. Belt and braces: whatever stops it,
        # the next poll or supervisor pass brings the live prices back.
        if self.streamer is not None:
            self._ensure_ticker()

    def instruments(self):
        """Only this feed's market. A crypto feed must never reach for NIFTY."""
        return config.instruments_in(self.market)

    def _idle(self):
        return time.time() - self.last_touch > IDLE_SECONDS

    def _set(self, **fields):
        with self.lock:
            self.state.update(fields)

    # -- the loop -----------------------------------------------------------
    _OHLC_TTL = {"5m": 60, "15m": 60, "1d": 600}

    def ohlc(self, name, interval="15m", days=None):
        """Candles at another timeframe, fetched when asked for and cached.

        The loop keeps one deep 15-minute window because that is the series
        the signal is computed on. A chart that also offers 5-minute and daily
        views needs two more series nothing else wants, so they are pulled on
        demand and held for a minute (ten for daily) rather than fetched on
        every pass - the alternative is three times the calls to Zerodha for
        two views most sessions never open.
        """
        key = (name, interval)
        hit = self._ohlc_cache.get(key)
        if hit and time.time() - hit[0] < self._OHLC_TTL.get(interval, 60):
            return hit[1]
        provider, state, _ = self._provider()
        if provider is None:
            return None
        df = self._provider_for(name, provider).get_ohlc(
            name, interval=interval,
            lookback_days=days or (250 if interval == "1d" else 5))
        df = drop_preopen(df, index_key=name)
        self._ohlc_cache[key] = (time.time(), df)
        return df

    # Daily candles cost 0.28s a symbol and five-minute 0.29s, so the whole
    # constituent list is about 16 seconds cold - a live endpoint with a cache
    # in front of it, not a nightly job. Held longer than the index series
    # because a breakout against 50 sessions does not change minute to minute.
    _HIST_TTL = {"day": 900, "5minute": 180, "minute": 120}
    # How long past that a cached set may still be served, while a fresh one
    # loads in the background, when the market is SHUT - the candles cannot
    # change then, and a cold read is about twenty seconds of Zerodha calls.
    # While it trades, the allowance is only twice the refresh time.
    _HIST_STALE_OK = {"day": 6 * 3600, "5minute": 20 * 60, "minute": 10 * 60}

    def constituent_history(self, interval="day", days=90):
        """Candles for every index constituent, cached.

        Reuses eq_tokens - the same dict the heat map streams from - rather
        than resolving the symbols a second time. Two rules for the same thing
        in one codebase is what put a 7px width on a labelled chip.
        """
        # Constituents are an Indian-market idea. Deribit has no index
        # members and no sectors - its equity_tokens() returns {} for exactly
        # that reason - so asking this of a crypto feed is a bug in the
        # caller, and it must say so rather than quietly answering with
        # another market's stocks.
        if self.market != "nse_index":
            raise RuntimeError(f"no index constituents in the {self.market} market")
        hit = self._hist_cache.get(interval)
        # A cached set only answers a request it covers: the cache is keyed by
        # interval, and one caller wants 120 days while another wants 400 - so
        # whichever asked first used to decide how far back the other's
        # 52-week highs could see.
        if hit and hit[2] >= days:
            age, ttl = time.time() - hit[0], self._HIST_TTL.get(interval, 300)
            if age < ttl:
                return hit[1]
            shut = not is_market_open(now_ist(), self._ref())
            if age < (self._HIST_STALE_OK.get(interval, 0) if shut else 2 * ttl):
                self._refresh_history_later(interval, hit[2])
                return hit[1]
        return self._load_history(interval, days)

    def _refresh_history_later(self, interval, days):
        """Fetch a fresh set in the background, at most one per interval."""
        with self._hist_refresh_lock:
            if interval in self._hist_refreshing:
                return
            self._hist_refreshing.add(interval)

        def run():
            try:
                self._load_history(interval, days)
            except Exception:
                pass
            finally:
                with self._hist_refresh_lock:
                    self._hist_refreshing.discard(interval)
        threading.Thread(target=run, daemon=True, name=f"hist:{interval}:{self.key}").start()

    def warm_history(self):
        """Read the constituent candles once, in the background, soon after a
        feed starts - so the first person to open Analytics or Momentum spikes
        after a restart is not the one who waits twenty seconds for them.
        The longest window any caller asks for, so the cache covers them all."""
        if self.market != "nse_index" or self._hist_warmed:
            return
        self._hist_warmed = True

        def run():
            time.sleep(15)                   # let the analysis loop get going first
            for interval, days in (("day", 400), ("5minute", 5)):
                if _stopping.is_set():
                    return
                try:
                    self.constituent_history(interval, days=days)
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True, name=f"warm:{self.key}").start()

    def _load_history(self, interval, days):
        token = user_kite.token_for(self.email)
        if not token:
            raise RuntimeError("no Zerodha token on this account")
        try:
            provider = KiteDataProvider(config.KITE_API_KEY, token)
        except Exception as exc:
            raise RuntimeError(f"could not reach Zerodha: {exc}")
        tokens = self.eq_tokens
        if not tokens:
            # Not the streamer's dict: that one is only filled once a socket
            # is up, and this endpoint is asked for by a page whether or not
            # anything is streaming.
            symbols = sorted({row[0] for rows in market_map.CONSTITUENTS.values()
                              for row in rows})
            tokens = provider.equity_tokens(symbols)
            self.eq_tokens = tokens or self.eq_tokens
        if not tokens:
            raise RuntimeError("no constituent tokens resolved")
        out, failed, first_error = {}, 0, None
        for sym, tok in tokens.items():
            try:
                df = provider.candles_for_token(tok, interval=interval, days=days)
            except Exception as exc:
                failed += 1
                if first_error is None:
                    first_error = f"{type(exc).__name__}: {exc}"
                continue
            if df is not None and len(df):
                out[sym] = df
        # A symbol with no candles and a call that cannot work are different
        # problems. Swallowing both as an empty dict is what let an empty Home
        # screen look like "no data yet" for two rounds.
        if failed and not out:
            raise RuntimeError(f"constituent history failed for all "
                               f"{failed} symbols - {first_error}")
        self._hist_last_error = first_error if failed else None
        self._hist_missing = failed
        if out:
            self._hist_cache[interval] = (time.time(), out, days)
        return out

    def crypto_pulse(self, name="BTC"):
        """The only "breadth" a one-instrument market has: where open interest
        sits across the live chain, and what the coin itself just did.

        Deribit publishes open interest per contract in the book summary, so
        the walls and the put/call ratio are real numbers, not a stand-in for
        the equity screens this market has no members for.
        """
        out = {"market": self.market, "index": name}
        ch = self.chain(name) or {}
        strikes = ch.get("strikes") or []
        spot = ch.get("spot")
        if strikes:
            ce = sum(s.get("call_oi") or 0 for s in strikes)
            pe = sum(s.get("put_oi") or 0 for s in strikes)
            top_c = max(strikes, key=lambda s: s.get("call_oi") or 0)
            top_p = max(strikes, key=lambda s: s.get("put_oi") or 0)
            near = sorted(strikes, key=lambda s: abs(s["strike"] - (spot or 0)))[:9]
            out.update({
                "expiry": ch.get("expiry"), "spot": spot,
                "call_oi": round(ce, 1), "put_oi": round(pe, 1),
                "pcr": round(pe / ce, 3) if ce else None,
                "call_wall": top_c["strike"], "put_wall": top_p["strike"],
                "strikes": [{"strike": s["strike"],
                             "ce": round(s.get("call_oi") or 0, 1),
                             "pe": round(s.get("put_oi") or 0, 1)}
                            for s in sorted(near, key=lambda s: s["strike"])]})
        return out

    def crypto_moves(self, name="BTC"):
        """What the coin did over the last five and ten minutes, on its own
        volume - the honest version of a spike screen for a market with one
        instrument rather than fifty."""
        out = {"market": self.market, "index": name}
        try:
            df = self.ohlc(name, "5m")
        except Exception as exc:
            return dict(out, error=str(exc)[:160])
        if df is None or len(df) < 6:
            return dict(out, note="Not enough five-minute candles yet.")
        c, v = df["Close"], df["Volume"]
        av = float(v.iloc[:-2].tail(20).mean() or 0)
        last = float(c.iloc[-1])
        rows = []
        for label, back in (("5 minutes", 2), ("10 minutes", 3), ("30 minutes", 7),
                            ("1 hour", 13)):
            if len(c) > back:
                rows.append({"window": label,
                             "move": round((last / float(c.iloc[-back]) - 1) * 100, 2)})
        out.update({
            "close": round(last, 2),
            "vx": round(float(v.iloc[-1]) / av, 2) if av else None,
            "high": round(float(df["High"].tail(288).max()), 2),
            "low": round(float(df["Low"].tail(288).min()), 2),
            "rows": rows})
        return out

    def chain(self, name):
        """The last option-chain snapshot for one instrument, or None.

        Already fetched every cycle for the signal itself - the strike ladder,
        the PCR and the walls all come out of it - and thrown away after. The
        screen can show it for the price of handing it over.
        """
        with self.lock:
            return self.base_oi.get(name)

    def _ref(self):
        """Any instrument of this feed's market - they share its session."""
        keys = self.instruments()
        return keys[0] if keys else None

    def _ticket_contract_ok(self, name):
        """True when opt_tokens[name] belongs to the ticket open NOW.

        It was cleared only when a ticket closed on a streamed tick. Closed any
        other way - cleared, the bell, the analysis pass - the next ticket
        inherited the old contract's stream, and its targets and stop would
        have been checked against a different option's price.
        """
        book = self.tickets.books.get(name)
        trade = book.trade if book else None
        if trade is None or trade["status"] != "OPEN":
            self.opt_tokens.pop(name, None)
            self.opt_for.pop(name, None)
            return False
        if self.opt_for.get(name) != trade.get("trade_id"):
            self.opt_tokens.pop(name, None)
            self.opt_for.pop(name, None)
        return True

    def _provider_for(self, name, kite_provider):
        """The right venue for one instrument.

        Crypto does not come from Zerodha and never will, so the feed holds one
        provider per market rather than one per user. Handing BTC to the Kite
        provider fails on every pass with an error about an instrument token
        that was never going to exist.
        """
        if config.market_for(name)["market_provider"] == "deribit":
            if self._crypto is None:
                self._crypto = DeribitDataProvider()
            return self._crypto
        return kite_provider

    def _provider(self):
        """Build this user's provider, or explain why we can't.

        Returns (provider, state, detail). A missing or dead token is not an
        error to retry loudly — it is a thing the user has to go and fix, so
        it is reported as feed state and the loop simply waits.
        """
        if _settings["mode"] == "free":
            return FreeDataProvider(), "ok", ""
        if config.MARKETS[self.market]["market_provider"] != "kite":
            # This market does not come from Zerodha, so a Zerodha token is not
            # a precondition for it. Checking one anyway is why a crypto feed
            # sat at feed="unknown" reporting "Insufficient permission for that
            # call" and analysed nothing: it was waiting on a credential it
            # never uses, for a venue that needs no credential at all.
            if self._crypto is None:
                self._crypto = DeribitDataProvider()
            return self._crypto, "ok", ""
        state, detail = user_kite.status(self.email)
        if state != "ok":
            return None, state, detail
        token = user_kite.token_for(self.email)
        if not token:
            return None, "missing", "Not connected to Zerodha yet."
        return KiteDataProvider(config.KITE_API_KEY, token), "ok", ""

    def _run(self):
        provider = None
        while not _stopping.is_set() and not self._idle():
            try:
                if provider is None:
                    provider, fstate, detail = self._provider()
                    if provider is None:
                        self._set(feed=fstate, error=detail)
                        self._sleep(20)
                        continue
                # `now` is required and must be IST — passing nothing raised a
                # TypeError that the outer handler swallowed, so the loop used
                # to fail silently on every pass and the page never updated.
                self.stage = "stream"
                self._start_stream()
                self.stage = "market-check"
                # THIS feed's market. Asked with no instrument, it answered for
                # the NSE session - so the crypto feed rang the 15:40 bell on an
                # open BTC ticket and reported "market closed" every evening.
                open_now = is_market_open(now_ist(), self._ref())
                for name in self.instruments():
                    if _stopping.is_set() or self._idle():
                        break
                    try:
                        self.stage = f"analyse:{name}"
                        rec, notes = fetch_recommendation(
                            self._provider_for(name, provider),
                            name, "15m",
                            # Enough sessions to warm the indicators and feed the
                            # per-second recompute after any holiday break;
                            # crypto trades every day, so its default is plenty.
                            (getattr(config, "INTRADAY_LOOKBACK_DAYS", None)
                             if self.market == "nse_index" else None),
                            quiet=True,
                            expiry=_settings["expiry"])
                        # The ticket engine sees every fresh reading — it is
                        # what decides whether this one becomes a ticket, and
                        # what checks any open ticket against its frozen
                        # levels. Fed before the snapshot is published so the
                        # page never shows a reading the tickets have not
                        # seen yet.
                        try:
                            evs = self.tickets.update(name, rec)
                        except Exception:
                            evs = []
                        with self.lock:
                            self.base_df[name] = rec.get("candles")
                            self.base_oi[name] = rec.get("option_chain")
                            self.state["indices"][name] = {
                                "rec": rec,
                                "public": _public(rec, name),
                                "why": explain.explain(rec),
                                "df": rec.get("candles"),
                                "notes": notes,
                                "at": now_ist().strftime("%H:%M:%S"),
                            }
                            self.state["error"] = None
                            for ev in evs:
                                ev["at"] = now_ist().strftime("%H:%M:%S")
                                self.events.insert(0, ev)
                            del self.events[30:]
                    except Exception as exc:
                        self._set(error=f"{name}: {exc}")
                    time.sleep(1.0)
                self._set(market_open=open_now, feed="ok",
                          updated=now_ist().strftime("%H:%M:%S"))
                # An intraday ticket does not survive the session. Squaring up
                # once at the close, rather than leaving rows marked OPEN
                # forever, is the difference between a record and a gap.
                if not open_now and not self.bell_closed:
                    try:
                        for ev in self.tickets.close_all_at_bell():
                            ev["at"] = now_ist().strftime("%H:%M:%S")
                            with self.lock:
                                self.events.insert(0, ev)
                    except Exception:
                        pass
                    self.bell_closed = True
                elif open_now:
                    self.bell_closed = False
                self.stage = "history"
                self._refresh_history(provider)
                self.stage = "idle"
            except Exception:
                self._set(error=traceback.format_exc(limit=2), feed="unknown")
                provider = None
                self._sleep(10)
                continue
            self._sleep(_settings["interval"])
        self._set(feed="idle")
        # A socket left open for a tab nobody has looked at in four minutes is
        # a subscription Zerodha is still counting.
        self.stop_stream()
        with _lock:
            if _feeds.get(self.key) is self:
                del _feeds[self.key]

    def _refresh_history(self, provider):
        """Pull the deep chart window, occasionally.

        Failures here are swallowed on purpose. The chart is a nicety; the
        signal is not, and a provider that rate-limits the extra call should
        cost you a scrollable chart rather than the thing you came for.
        """
        if time.time() - self.hist_at < HISTORY_TTL:
            return
        for name in self.instruments():
            if _stopping.is_set() or self._idle():
                return
            try:
                df = self._provider_for(name, provider).get_ohlc(
                    name, interval="15m", lookback_days=HISTORY_DAYS)
                # Dropped for the same reason the signal drops them: the
                # pre-open auction bar is priced on almost no volume, and a
                # chart that shows it puts a spike on every single morning.
                df = drop_preopen(df, index_key=name)
                if df is not None and len(df) > 1:
                    with self.lock:
                        self.hist[name] = df
            except Exception:
                pass
            time.sleep(0.5)
        self.hist_at = time.time()

    # ------------------------------------------------------------ streaming
    def _start_stream(self):
        """Open the tick socket for this user, once, and subscribe the three
        indices.

        Failures are not fatal. Streaming makes the numbers move; the analysis
        underneath it works without one, so a socket that will not open costs
        you smoothness rather than the tool.
        """
        if self.streamer is not None or _settings["mode"] != "kite":
            return
        if config.MARKETS[self.market]["market_provider"] != "kite":
            # Not a Zerodha market. Opening a KiteStreamer here subscribed the
            # three NSE indices to a crypto feed, so /api/tick answered a crypto
            # screen with NIFTY, BANKNIFTY and SENSEX and nothing at all for
            # BTC. Crypto gets its own price loop below instead.
            # Mark the venue BEFORE the thread starts. The loop's first act is
            # to read self.streamer, and setting it afterwards meant the thread
            # saw None, returned immediately, and the price was whatever the
            # single call below had fetched - frozen, with the age climbing.
            self.streamer = _NO_STREAM
            self._start_crypto_stream()
            self._ensure_ticker()
            return
        token = user_kite.token_for(self.email)
        if not token:
            self.stream_error = "no Zerodha token on this account"
            return
        try:
            st = KiteStreamer(config.KITE_API_KEY, token)
            if not st.start():
                self.stream_error = f"ticker would not start: {st.last_error}"
                return
        except Exception as exc:
            self.stream_error = f"ticker failed: {exc}"
            return
        # There was a REST provider built here purely to prove the token, and
        # a failure tore the socket back down. Now that the token lookup
        # retries on its own that check does more harm than good: it would
        # throw away a socket that had already connected - proof enough that
        # the token is good - over something the next pass would have fixed.
        self.streamer = st
        self._stream_born = time.time()
        self.stream_error = None
        self._subscribe_indices()
        self._ensure_ticker()

    def _ensure_ticker(self):
        """Start the tick loop unless one is already running.

        It is started from three places - the first stream, the crypto branch,
        and a page poll through touch() - and touch() could see the socket set
        a moment before _start_stream made its thread, start one of its own,
        and have it overwritten: two loops on one feed, both reading the same
        socket and checking the same ticket targets. Seen on 15 Sep 2026.
        """
        with self._ticker_lock:
            if self.ticker is not None and self.ticker.is_alive():
                return
            self.ticker = threading.Thread(target=self._tick_loop, daemon=True,
                                           name=f"ticks:{self.key}")
            self.ticker.start()

    def _note_fault(self, where, text):
        """Remember what stopped the fast loop or the live recompute, and write
        it to the log - once every five minutes for each distinct fault.

        Both used to swallow every exception, so on 15 Sep 2026 the live
        recompute stopped updating the page mid-session and nothing said why.
        """
        now = time.time()
        self.live_errors[where] = (text, now)
        key = (where, text)
        if now - self._fault_said.get(key, 0.0) < 300.0:
            return
        self._fault_said[key] = now
        import traceback as _tb
        tb = _tb.format_exc()
        print(f"[live] {now_ist():%Y-%m-%d %H:%M:%S} {self.market} {where}: {text}", flush=True)
        if tb and not tb.startswith("NoneType: None"):
            print(tb.rstrip(), flush=True)

    def _live_status(self):
        """How long since the live recompute last updated an index, and what
        has recently stopped it - for /api/tick and for anyone checking."""
        now = time.time()
        return {"age": round(now - self.live_at, 1) if self.live_at else None,
                "errors": {k: {"what": v[0], "age": round(now - v[1], 1)}
                           for k, v in list(self.live_errors.items()) if now - v[1] < 600}}

    def _start_crypto_stream(self):
        """Open Deribit's socket and take the index for every instrument.

        Replaces a once-a-second REST poll. The index alone was smooth enough
        to watch, but the premium only moved when the analysis ran - and the
        premium is the number a buyer actually pays.
        """
        if self.dstream is not None:
            return
        st = DeribitStreamer()
        if not st.start():
            self.stream_error = f"deribit socket: {st.last_error}"
            return
        self.dstream = st
        self.stream_error = None
        st.subscribe([f"deribit_price_index.{config.INSTRUMENTS[n]['deribit_index']}"
                      for n in self.instruments()])

    def _crypto_suggested(self, name):
        """Stream the premium of the strike currently being suggested.

        The crypto twin of _subscribe_suggested: most of the day there is no
        ticket, only a suggestion, and that is exactly when someone is deciding
        whether to take it.
        """
        st = self.dstream
        if st is None:
            return
        with self.lock:
            entry = self.state["indices"].get(name)
            pub = (entry or {}).get("public") or {}
        strike, opt = pub.get("strike"), pub.get("option_type")
        if not strike or not opt:
            # No suggestion any more, so no suggested premium. Keeping the last
            # one left the price of a contract nobody is being shown - after a
            # PE signal went neutral, its put kept feeding the tick endpoint.
            with self.lock:
                self.sug_px.pop(name, None)
            self.sug_tokens.pop(name, None)
            return
        try:
            inst = self._provider_for(name, None).option_instrument(name, strike, opt)
        except Exception:
            inst = None
        if not inst:
            return
        have = self.sug_tokens.get(name)
        # Compared on the full contract name, not strike and side: the expiry
        # is chosen by spread and can change while the strike stays the same.
        if have and have[2] == inst:
            return                            # already streaming this one
        self.sug_tokens[name] = (strike, opt, inst)
        st.subscribe([f"ticker.{inst}.100ms"])

    def _crypto_ticket(self, name):
        """Stream the contract an OPEN ticket is actually tracked on.

        Its strike is frozen at entry and drifts away from whatever is being
        suggested now, so the suggested-strike subscription is not enough - the
        crypto twin of _subscribe_ticket.
        """
        st = self.dstream
        if st is None or not self._ticket_contract_ok(name) or self.opt_tokens.get(name) is not None:
            return
        book = self.tickets.books.get(name)
        trade = book.trade if book else None
        if trade is None or trade["status"] != "OPEN" or not trade.get("use_premium"):
            return
        try:
            inst = self._provider_for(name, None).option_instrument(
                name, trade["strike"], trade["option_type"], trade.get("expiry"))
        except Exception:
            inst = None
        if not inst:
            return
        self.opt_tokens[name] = inst
        self.opt_for[name] = trade.get("trade_id")
        st.subscribe([f"ticker.{inst}.100ms"])

    def _crypto_track(self):
        """Price an open ticket and check its levels, on the tick.

        This was missing entirely. A ticket's live price and its targets were
        only ever touched by the analysis pass, so NOW crawled behind a premium
        arriving several times a second - and, worse, a stop could be blown
        through between passes and stamped with whatever price it had by the
        time anyone looked.
        """
        st = self.dstream
        if st is None:
            return
        for name in self.instruments():
            self._crypto_ticket(name)
            inst = self.opt_tokens.get(name)
            if not inst:
                continue
            px = st.mark_usd(inst, config.INSTRUMENTS[name]["deribit_index"])
            if px is None:
                continue
            self.tickets.live_price(name, px)
            for ev in self.tickets.tick_price(name, px):
                ev["at"] = now_ist().strftime("%H:%M:%S")
                with self.lock:
                    self.events.insert(0, ev)
                    del self.events[30:]
                if ev.get("kind") == "closed":
                    self.opt_tokens.pop(name, None)

    def _crypto_prices(self):
        """Read the socket's memory into this feed's price maps."""
        st = self.dstream
        if st is None:
            return
        self._crypto_at = time.time()
        self._crypto_track()
        for name in self.instruments():
            meta = config.INSTRUMENTS[name]
            px = st.index_price(meta["deribit_index"])
            if px is not None:
                with self.lock:
                    self.spots[name] = float(px)
                self._crypto_seen = time.time()
            self._crypto_suggested(name)
            ent = self.sug_tokens.get(name)
            if ent:
                mk = st.mark_usd(ent[2], meta["deribit_index"])
                if mk is not None:
                    with self.lock:
                        self.sug_px[name] = float(mk)

    def _kite_watchdog(self, st):
        """Rebuild the Zerodha socket if it has gone silent during the session.

        KiteTicker reconnects on its own, but not from everything. This morning
        the Mac half-woke at 09:19 with its network not yet usable, the socket
        failed its opening handshake, and no tick arrived for the next sixteen
        minutes of a trading session while the page looked healthy - the
        analysis kept refreshing every thirty seconds, so the numbers did change,
        just never live. Silence during the session is now treated as a fault.
        """
        if st is None or st is _NO_STREAM:
            return
        now = time.time()
        age = st.age_seconds()
        if age is not None and age < STALL_SECONDS:
            # Healthy again: forget the backoff and any stale complaint.
            self._rebuild_gap = STALL_SECONDS
            if self.stream_error and self.stream_error.startswith("tick socket"):
                self.stream_error = None
            return
        if now - self._stream_born < STALL_SECONDS:
            return                                # still connecting
        keys = self.instruments()
        if not keys or not is_market_open(now_ist(), keys[0]):
            return                                # no ticks expected
        if now - self._last_rebuild < self._rebuild_gap:
            return
        self._last_rebuild = now
        self._rebuild_gap = min(self._rebuild_gap * 2, 300)
        self._rebuild_kite_stream(st)

    def _rebuild_kite_stream(self, old):
        """Swap in a fresh socket carrying every token the old one had.

        Resubscribing from the feed's own token maps rather than re-resolving
        them keeps this to one connection and no instrument-dump downloads.
        """
        token = user_kite.token_for(self.email)
        if not token:
            self.stream_error = "tick socket silent and no Zerodha token to rebuild it"
            return
        try:
            new = KiteStreamer(config.KITE_API_KEY, token)
            if not new.start():
                self.stream_error = f"tick socket rebuild failed: {new.last_error}"
                return
        except Exception as exc:
            self.stream_error = f"tick socket rebuild failed: {exc}"
            return
        toks = (list(self.tokens.values()) + list(self.opt_tokens.values())
                + [v[2] for v in self.sug_tokens.values()]
                + list(self.eq_tokens.values()))
        new.subscribe([t for t in toks if t], quote=True)
        self.streamer = new
        self._stream_born = time.time()
        self.stream_error = (f"tick socket rebuilt at {now_ist():%H:%M:%S} "
                             "after it went silent")
        # Stop the old one retrying in the background, or Zerodha's limit of
        # three connections per token fills up with zombies.
        try:
            old._kws.stop_retry()
        except Exception:
            pass
        try:
            old.stop()
        except Exception:
            pass
        print(f"[stream] {now_ist():%Y-%m-%d %H:%M:%S} rebuilt the Zerodha tick "
              f"socket for {self.email} ({len(toks)} tokens)", flush=True)

    def _subscribe_indices(self):
        """Resolve the three index tokens and put them on the feed, retrying
        until every one of them is known.

        This used to happen once, inline in _start_stream. Resolving a token
        means downloading Zerodha's entire instrument dump, and on the morning
        that call failed - a timeout, a rate limit, one slow response - the map
        stayed empty for the whole life of the process with nothing anywhere to
        try it again. The failure was silent and its blast radius was most of
        the tool: no streamed spot, no forming candle, and every indicator left
        recomputing on closed fifteen-minute bars. That is the whole of "the
        numbers aren't live", out of one unretried lookup.
        """
        st = self.streamer
        if st is None:
            return
        missing = [n for n in config.instruments_in('nse_index') if not self.tokens.get(n)]
        if not missing:
            return                     # the ordinary case, and it costs nothing
        # The dump is heavy, so a failure waits instead of retrying at tick rate.
        if time.time() - self._idx_tried < 30.0:
            return
        self._idx_tried = time.time()
        token = user_kite.token_for(self.email)
        if not token:
            return
        try:
            provider = KiteDataProvider(config.KITE_API_KEY, token)
        except Exception:
            return
        toks = []
        for name in missing:
            try:
                t = provider.index_token(name)
            except Exception:
                t = None
            if t:
                with self.lock:
                    self.tokens[name] = t
                toks.append(t)
        if toks:
            try:
                st.subscribe(toks, quote=True)
            except Exception:
                pass

    def _subscribe_chain(self, name):
        """Put the option chain's strikes around the money on the live feed.

        The chain is a snapshot taken with each analysis pass - about every
        forty seconds - so its prices stood still between passes while the
        index moved under them. Its contracts are ordinary instruments on the
        same socket: streamed in FULL mode, the one carrying open interest and
        the order book, they move tick by tick like the index does.

        Tokens come from one pass over the instrument list per expiry. The
        window follows the money as spot drifts; a strike that falls out of it
        keeps streaming, a few tokens against Zerodha's 3,000 a socket. A new
        socket - a rebuild or a restart of the stream - is subscribed afresh.
        """
        st = self.streamer
        if st is None or st is _NO_STREAM:
            return
        if config.market_for(name)["market_provider"] != "kite":
            return
        with self.lock:
            chain = self.base_oi.get(name)
        if not chain or not chain.get("strikes") or not chain.get("expiry"):
            return
        expiry = str(chain["expiry"])
        have = self.chain_toks.get(name)
        if not have or have.get("expiry") != expiry:
            tried = self._chain_tried.get(name)
            if tried and tried[0] == expiry and time.time() - tried[1] < 60.0:
                return                  # this expiry's lookup failed a moment ago
            self._chain_tried[name] = (expiry, time.time())
            token = user_kite.token_for(self.email)
            if not token:
                return
            try:
                mp = KiteDataProvider(config.KITE_API_KEY, token).chain_tokens(name, expiry)
            except Exception:
                mp = {}
            if not mp:
                return
            self._chain_tried.pop(name, None)   # the wait is for failures only
            have = {"expiry": expiry, "map": mp, "streamed": set(), "on": st}
            self.chain_toks[name] = have
        if have.get("on") is not st:
            have["streamed"], have["on"] = set(), st
        spot = chain.get("spot")
        if spot is None:
            return
        strikes = sorted(float(x["strike"]) for x in chain["strikes"])
        step = (config.INSTRUMENTS.get(name) or {}).get("strike_step") or 50
        atm = min(strikes, key=lambda k: abs(k - spot))
        want = [have["map"].get((k, kind)) for k in strikes
                if abs(k - atm) <= CHAIN_STREAM_SPAN * step for kind in ("CE", "PE")]
        fresh = [t for t in want if t and t not in have["streamed"]]
        if not fresh:
            return
        try:
            st.subscribe(fresh, full=True)
        except Exception:
            return
        have["streamed"].update(fresh)

    def chain_live(self, name):
        """{(strike, "CE"/"PE"): {ltp, oi, bid, ask, at}} for the chain's streamed
        strikes that have a recent tick. Empty outside the session: after the
        bell a socket keeps sending packets whose prices no longer move, and a
        chain marked live over them would say something untrue."""
        st = self.streamer
        have = self.chain_toks.get(name)
        if (st is None or st is _NO_STREAM or not have or have.get("on") is not st
                or not hasattr(st, "book")):
            return {}
        if not is_market_open(now_ist(), name):
            return {}
        out = {}
        for key, tok in list(have["map"].items()):
            if tok in have["streamed"]:
                b = st.book(tok, max_age=CHAIN_TICK_MAX_AGE)
                if b:
                    out[key] = b
        return out

    def _subscribe_ticket(self, name):
        """Add an open ticket's own contract to the feed.

        The index token is not enough: a ticket is tracked on the premium of
        one specific strike, and that contract has its own price, which is the
        one the P&L is actually made of.
        """
        if (self.streamer is None or not self._ticket_contract_ok(name)
                or self.opt_tokens.get(name) is not None):
            return
        book = self.tickets.books.get(name)
        trade = book.trade if book else None
        if trade is None or trade["status"] != "OPEN" or not trade.get("use_premium"):
            return
        token = user_kite.token_for(self.email)
        if not token:
            return
        try:
            provider = KiteDataProvider(config.KITE_API_KEY, token)
            tok = provider.option_token(trade["index"], trade["strike"],
                                        trade["option_type"])
        except Exception:
            tok = None
        if not tok:
            return
        self.opt_tokens[name] = tok
        self.opt_for[name] = trade.get("trade_id")
        try:
            self.streamer.subscribe([tok], quote=True)
        except Exception:
            pass

    def _subscribe_suggested(self, name):
        """Stream the premium of the strike currently being SUGGESTED.

        An open ticket has its contract subscribed already, but most of the
        day there is no ticket — only a suggestion — and that is exactly when
        someone is deciding whether to take it. Leaving its premium to the
        thirty-second analysis meant the one number a buyer actually pays sat
        still while everything around it moved.

        The suggestion changes as spot drifts across strikes, so this
        re-resolves when it does and keeps the handful of tokens it has seen.
        """
        if self.streamer is None:
            return
        with self.lock:
            entry = self.state["indices"].get(name)
            pub = (entry or {}).get("public") or {}
        strike, opt = pub.get("strike"), pub.get("option_type")
        if not strike or not opt:
            # No suggestion any more, so no suggested premium. Keeping the last
            # one left the price of a contract nobody is being shown - after a
            # PE signal went neutral, its put kept feeding the tick endpoint.
            with self.lock:
                self.sug_px.pop(name, None)
            self.sug_tokens.pop(name, None)
            return
        have = self.sug_tokens.get(name)
        if have and have[0] == strike and have[1] == opt:
            return                     # already streaming this one
        token = user_kite.token_for(self.email)
        if not token:
            return
        try:
            provider = KiteDataProvider(config.KITE_API_KEY, token)
            tok = provider.option_token(name, strike, opt)
        except Exception:
            tok = None
        if not tok:
            return
        self.sug_tokens[name] = (strike, opt, tok)
        try:
            self.streamer.subscribe([tok], quote=True)
        except Exception:
            pass

    # ------------------------------------------------- the live recompute
    def _df_with_live_bar(self, name):
        """The completed REST candles with the in-progress one, assembled from
        the tick stream, appended on the end.

        This is what makes the indicators live. Without it EMA, MACD, RSI, ADX
        and VWAP can only move when a whole fifteen-minute candle closes and
        gets fetched — which is exactly why the website's gauges sat still
        while the desktop's moved.
        """
        with self.lock:
            df = self.base_df.get(name)
        if df is None or df.empty:
            return None
        st = self.streamer
        if st is _NO_STREAM:
            # Same idea, different venue. Without this the crypto indicators
            # only moved when the candles were refetched, which on a market
            # that never closes is the one place you would notice.
            ds = self.dstream
            bar = (ds.forming_bar(config.INSTRUMENTS[name]["deribit_index"])
                   if ds else None)
            if not bar:
                return df
        else:
            tok = self.tokens.get(name)
            if st is None or not tok:
                return df
            bar = st.forming_bar(tok)
            if not bar:
                return df
        try:
            ts = pd.Timestamp(bar["start"], unit="s", tz="UTC").tz_convert(
                df.index.tz or "Asia/Kolkata")
            # REST usually hands back the current, incomplete candle too — drop
            # it so the streamed one replaces it rather than duplicating it.
            if len(df) and df.index[-1] >= ts:
                df = df.iloc[:-1]
            if df.empty:
                return None
            # Indices carry no real volume, and VWAP treats 0 as "unknown" and
            # forward-fills. Carrying the previous bar's figure keeps the
            # forming bar behaving like every other one rather than punching a
            # hole in it. An estimate, and it only affects VWAP.
            vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0.0
            row = pd.DataFrame(
                {"Open": [bar["o"]], "High": [bar["h"]], "Low": [bar["l"]],
                 "Close": [bar["c"]], "Volume": [vol]}, index=[ts])
            return pd.concat([df, row])
        except Exception:
            return df

    def _live_analysis(self):
        """Recompute every index on its live candle, about once a second.

        The option chain is NOT re-fetched — it comes over REST and cannot
        change faster than the poll that gets it. What is recomputed is
        everything derived from price, which is the part that moves.
        """
        for name in self.instruments():
            df = self._df_with_live_bar(name)
            if df is None or len(df) < 60:
                if self.base_df.get(name) is not None:
                    self._note_fault(name, f"only {0 if df is None else len(df)} candles "
                                           "for the live recompute")
                continue
            with self.lock:
                oi = self.base_oi.get(name)
            if not isinstance(oi, dict) or "available" not in oi:
                oi = signal_engine.compute_option_chain_signal(None)
            try:
                tech = signal_engine.compute_technical_signal(df)
                spot = float(df["Close"].iloc[-1])
                step = config.INSTRUMENTS[name]["strike_step"]
                try:
                    # ADX is already in tech — passing it stops reachability
                    # recomputing it once per index per second.
                    reach = signal_engine.compute_reachability(
                        spot, oi, df, now_ist(), adx=tech.get("adx"),
                        index_key=name)
                except Exception:
                    reach = None
                rec = signal_engine.build_recommendation(name, tech, oi, step,
                                                          reach=reach)
                try:
                    rec["trend"] = signal_engine.compute_market_trend(df)
                except Exception:
                    rec["trend"] = None
                rec["candles"] = df
                rec["opening_range"] = signal_engine.opening_range(df)
            except Exception as exc:
                self._note_fault(name, f"{type(exc).__name__}: {exc}")
                continue

            try:
                evs = self.tickets.update(name, rec)
            except Exception as exc:
                self._note_fault(f"{name} tickets", f"{type(exc).__name__}: {exc}")
                evs = []
            with self.lock:
                entry = self.state["indices"].get(name)
                if entry is None:
                    continue
                entry["rec"] = rec
                entry["public"] = _public(rec, name)
                entry["why"] = explain.explain(rec)
                entry["at"] = now_ist().strftime("%H:%M:%S")
                self.live_at = time.time()
                self.live_errors.pop(name, None)
                for ev in evs:
                    ev["at"] = entry["at"]
                    self.events.insert(0, ev)
                del self.events[30:]

    def forming(self):
        """The in-progress candle per index, so the chart's last bar can move
        instead of waiting for the next fetch."""
        st = self.streamer
        if st is _NO_STREAM:
            ds = self.dstream
            if ds is None:
                return {}
            out = {}
            for name in self.instruments():
                bar = ds.forming_bar(config.INSTRUMENTS[name]["deribit_index"])
                if bar:
                    out[name] = {"t": int(bar["start"]), "o": bar["o"],
                                 "h": bar["h"], "l": bar["l"], "c": bar["c"]}
            return out
        if st is None:
            return {}
        out = {}
        for name, tok in list(self.tokens.items()):
            bar = st.forming_bar(tok)
            if bar:
                out[name] = {"t": int(bar["start"]), "o": bar["o"], "h": bar["h"],
                             "l": bar["l"], "c": bar["c"]}
        return out

    def _subscribe_constituents(self):
        """Stream every index constituent, for the heat map.

        Fifty-seven symbols across the three indices, subscribed in QUOTE mode
        so each tick carries the day's percentage change directly rather than
        making us remember yesterday's close for all of them.

        Tried once. If the instrument dump fails there is no point retrying it
        every second — the map goes without, and everything else is unaffected.
        """
        if self._eq_tried or self.streamer is None:
            return
        self._eq_tried = True
        symbols = sorted({row[0] for rows in market_map.CONSTITUENTS.values()
                          for row in rows})
        token = user_kite.token_for(self.email)
        if not token:
            self._eq_tried = False        # no token yet; worth trying again
            return
        try:
            provider = KiteDataProvider(config.KITE_API_KEY, token)
            found = provider.equity_tokens(symbols)
        except Exception:
            return
        if not found:
            return
        self.eq_tokens = found
        try:
            self.streamer.subscribe(list(found.values()), quote=True)
        except Exception:
            pass

    def heat_map(self, name, width=900, height=460):
        """The treemap for one index, laid out at the size the browser asked
        for.

        The layout is computed here rather than in the page because squarify()
        already exists and is tested — porting it to JavaScript would mean two
        implementations of the same arithmetic, and the one in the browser
        would be the untested one.
        """
        rows = market_map.rows_for(name, self.eq_tokens, self.streamer)
        rows = [r for r in rows if (r.get("weight") or 0) > 0]
        if not rows:
            return {"tiles": [], "breadth": None, "ready": bool(self.eq_tokens)}
        # Biggest first, which is what makes a squarified treemap readable.
        rows.sort(key=lambda r: -r["weight"])
        rects = market_map.squarify([r["weight"] for r in rows],
                                    0, 0, float(width), float(height))
        tiles = []
        for r, rect in zip(rows, rects):
            x, y, w, h = rect
            tiles.append({"sym": r["sym"], "sector": r["sector"],
                          "weight": r["weight"], "pct": r["pct"],
                          "x": round(x, 1), "y": round(y, 1),
                          "w": round(w, 1), "h": round(h, 1)})
        known = [r for r in rows if r.get("pct") is not None]
        breadth = None
        if known:
            wsum = sum(r["weight"] for r in known) or 1
            breadth = {
                "up": sum(1 for r in known if r["pct"] > 0),
                "down": sum(1 for r in known if r["pct"] < 0),
                "flat": sum(1 for r in known if r["pct"] == 0),
                "known": len(known), "total": len(rows),
                "weighted": round(sum(r["pct"] * r["weight"]
                                      for r in known) / wsum, 2),
            }
        return {"tiles": tiles, "breadth": breadth,
                "ready": bool(self.eq_tokens)}

    def _tick_loop(self):
        """Read the socket's memory a few times a second and act on it.

        No network happens here — the kiteconnect library fills a dict on its
        own background thread and this only reads it, which is why it can run
        at this rate without costing anything.
        """
        # Runs until the feed is actually shut down (stop_stream sets streamer
        # to None), NOT merely until it looks idle. It used to exit on _idle(),
        # and when the Mac slept and woke the clock jumped past the idle limit
        # for a moment: this loop quit, the main loop carried on once the
        # supervisor touched it again, and nothing restarted this one. On
        # 11 Sep the BTC price then sat frozen for over an hour while charts
        # and signals kept updating from the slower analysis pass.
        while not _stopping.is_set():
            st = self.streamer
            if st is None:
                return
            if st is _NO_STREAM:
                # No push socket for this venue, so the price is polled. Deribit
                # publishes an index over REST and answers in tens of
                # milliseconds; once a second is smooth enough to watch and
                # gentle enough not to get rate-limited, which four times a
                # second would not be.
                try:
                    # Reading the socket's dict is a memory read, so this runs
                    # at the loop rate rather than on a timer of its own.
                    self._crypto_prices()
                    if time.time() - self._last_live > 1.0:
                        self._last_live = time.time()
                        self._live_analysis()
                except Exception as exc:
                    self._note_fault("tick loop", f"{type(exc).__name__}: {exc}")
                time.sleep(0.25)
                continue
            try:
                self._kite_watchdog(st)
                st = self.streamer
                self._subscribe_indices()
                self._subscribe_constituents()
                for name, tok in list(self.tokens.items()):
                    px = st.price(tok)
                    if px is not None:
                        with self.lock:
                            self.spots[name] = px
                for name in list(self.instruments()):
                    self._subscribe_suggested(name)
                    sug = self.sug_tokens.get(name)
                    if sug:
                        px = st.price(sug[2])
                        if px is not None:
                            with self.lock:
                                self.sug_px[name] = px
                    self._subscribe_ticket(name)
                    self._subscribe_chain(name)
                    tok = self.opt_tokens.get(name)
                    if not tok:
                        continue
                    px = st.price(tok)
                    if px is None:
                        continue
                    self.tickets.live_price(name, px)
                    # Targets are checked on the tick, so one reached at
                    # 11:44:09 is stamped 11:44:09 rather than at the next
                    # poll with whatever price it had by then.
                    for ev in self.tickets.tick_price(name, px):
                        ev["at"] = now_ist().strftime("%H:%M:%S")
                        with self.lock:
                            self.events.insert(0, ev)
                            del self.events[30:]
                        if ev.get("kind") == "closed":
                            self.opt_tokens.pop(name, None)
                # Everything derived from price, recomputed on the forming
                # candle — the same thing the desktop does every second, and
                # the reason its gauges move and the website's did not.
                if time.time() - self._last_live > 1.0:
                    self._last_live = time.time()
                    self._live_analysis()
            except Exception as exc:
                self._note_fault("tick loop", f"{type(exc).__name__}: {exc}")
            # A target reached is stamped at the moment this loop sees the
            # price, so this interval is also the worst-case error on every
            # "T1 hit at 11:44:09" written into the log.
            time.sleep(0.25)

    def ticks(self):
        """The newest streamed prices, for the page's fast poll.

        Read straight out of the socket rather than out of the copies the
        half-second loop leaves behind. Those copies were up to half a second
        old before the page had even asked for them, and the page then held
        them for its own poll interval on top - so a price could be a second
        and a half behind the exchange while every layer looked healthy. The
        socket's own dict is filled on the library's thread as ticks land, and
        reading it is a dict lookup, so there is nothing to pay for freshness.
        """
        st = self.streamer
        with self.lock:
            spots = dict(self.spots)
            sug = dict(self.sug_px)
        if st is _NO_STREAM:
            # Polled, not pushed. "Live" still has to mean live, so it is
            # measured the same way - how long since a price actually arrived -
            # just against the poll instead of a socket.
            age = (time.time() - self._crypto_seen) if self._crypto_seen else None
            prem = {}
            if self.dstream is not None:
                for nm, inst in list(self.opt_tokens.items()):
                    v = self.dstream.mark_usd(
                        inst, config.INSTRUMENTS[nm]["deribit_index"])
                    if v is not None:
                        prem[nm] = v
            return {"spots": spots, "ltp": sug, "premium": prem,
                    "live": bool(age is not None and age < 15.0),
                    "age": round(age, 1) if age is not None else None,
                    "bar": self.forming(),
                    "stream_error": (self.stream_error or
                                     (self.dstream.last_error
                                      if self.dstream and not self.dstream.connected
                                      else None)),
                    "stage": self.stage,
                    "live_analysis": self._live_status(),
                    "streamed": True}
        if st is not None:
            for name, tok in list(self.tokens.items()):
                px = st.price(tok)
                if px is not None:
                    spots[name] = px
            for name, ent in list(self.sug_tokens.items()):
                px = st.price(ent[2])
                if px is not None:
                    sug[name] = px
        age = None
        if st is not None:
            try:
                age = st.age_seconds()
            except Exception:
                age = None
        # "Live" has to mean live, and it takes all three of these.
        #
        # A socket can stay open and stop delivering, so connected alone is not
        # enough - hence the age. But Zerodha also keeps pushing tick packets
        # long after the bell with last_price unchanged: measured at 16:00,
        # twenty minutes past the close, age was still 0.1s while every index
        # sat on its closing value. Packets arriving is not the same as prices
        # moving, and without the session check the badge read LIVE in green
        # over numbers that were done for the day - the precise claim it exists
        # to prevent. The auction still counts as open: options trade to 15:40.
        out = {"spots": spots,
               "live": bool(st is not None and st.connected
                            and age is not None and age < 15.0
                            and is_market_open(now_ist(), self._ref())),
               "age": round(age, 1) if age is not None else None,
               "premium": {}, "ltp": sug, "bar": self.forming(),
               "stream_error": self.stream_error, "stage": self.stage,
               "live_analysis": self._live_status()}
        if st is not None:
            for name, tok in list(self.opt_tokens.items()):
                px = st.price(tok)
                if px is not None:
                    out["premium"][name] = px
        return out

    def stop_stream(self):
        st, self.streamer = self.streamer, None
        if st is not None and st is not _NO_STREAM:
            try:
                st.stop()
            except Exception:
                pass
        # The crypto socket is a separate object and was being left open when a
        # feed was reaped - a reaped-and-restarted feed would have leaked one
        # connection to Deribit per cycle.
        ds, self.dstream = self.dstream, None
        if ds is not None:
            try:
                ds.stop()
            except Exception:
                pass

    def _sleep(self, seconds):
        """Wait, but come back early if we're stopping or something woke us."""
        if _stopping.wait(0):
            return
        if self.wake.wait(seconds):
            self.wake.clear()

    # -- reading ------------------------------------------------------------
    def _tickets_with_odds(self):
        """Each index's ticket state, an open ticket carrying the chance of
        reaching its own frozen levels from where the index is now."""
        out = {}
        for k, v in self.state["indices"].items():
            pub = self.tickets.public(k)
            t = pub.get("ticket")
            if t and t.get("open"):
                t["odds"] = _ticket_odds(t, v.get("public"))
                if t.get("tracked_on") == "premium":
                    t["charges"] = _trade_charges(
                        k, t.get("entry"),
                        (list(t.get("targets") or []) + [None] * 3)[:3] + [t.get("stop")],
                        t.get("lot_size"))
            out[k] = pub
        return out

    def snapshot(self):
        with self.lock:
            return {
                "market_open": self.state["market_open"],
                # Computed here rather than stored, so it is right the moment
                # it is read instead of at the last analysis pass.
                "closing_auction": config.in_closing_auction(now_ist()),
                "updated": self.state["updated"],
                "feed": self.state["feed"],
                "error": self.state["error"],
                "indices": {k: v["public"] for k, v in self.state["indices"].items()},
                "why": {k: v["why"] for k, v in self.state["indices"].items()},
                "tickets": self._tickets_with_odds(),
                "session": self.tickets.session(),
                "events": list(self.events[:8]),
            }

    def candles(self, name):
        """(DataFrame, recommendation) for the chart, or (None, None).

        The deep window when there is one, so the chart can be scrolled back —
        falling back to the signal's own bars, which are the same series, just
        shorter. Either way the levels come from the same recommendation.
        """
        with self.lock:
            entry = self.state["indices"].get(name)
            if not entry:
                return None, None
            live = entry["df"]
            deep = self.hist.get(name)
            # Explicit None checks: a DataFrame in a boolean context raises
            # rather than being falsy, so `deep or live` would blow up here.
            n_live = 0 if live is None else len(live)
            n_deep = 0 if deep is None else len(deep)
            return (deep if n_deep > n_live else live), entry["rec"]


# ---------------------------------------------------------------------------
def for_user(email, market=None, start=True):
    """This user's feed for one market, started if it isn't running. None if
    `start` is off and there is nothing running — used by callers that want to
    read without bringing a feed to life, such as the operator page."""
    market = market or config.DEFAULT_MARKET
    key = _key_for(email, market)
    with _lock:
        feed = _feeds.get(key)
        if feed is None:
            if not start:
                return None
            feed = _feeds[key] = Feed(key, email, market)
    if start:
        feed.touch()
    return feed


def wake(email):
    """Pick up a token that has just been connected, instead of sitting out
    the rest of the back-off. Without this you finish the Zerodha login and
    the page keeps saying 'not connected' for another twenty seconds, which
    reads exactly like the login having failed."""
    feed = for_user(email, start=False)
    if feed:
        feed.wake.set()


def active():
    with _lock:
        return sorted(_feeds)


def stop_all():
    _stopping.set()
    with _lock:
        for feed in _feeds.values():
            feed.wake.set()
            feed.stop_stream()
