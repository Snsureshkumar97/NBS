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
from data_providers import (DeribitDataProvider, FreeDataProvider,
                            KiteDataProvider, KiteStreamer)
from main import drop_preopen, fetch_recommendation, is_market_open, now_ist

# A feed with nobody watching it is switched off after this long. Generous
# enough to survive a user reading the explanation for a few minutes without
# the page reloading, short enough that a closed tab stops calling Zerodha.
IDLE_SECONDS = 240

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
        "premium_targets": rec.get("premium_targets") or [None, None, None],
        "premium_stop": rec.get("premium_stop_loss"),
        "premium_source": rec.get("premium_source"),
        "atm_strike": rec.get("atm_strike"),
        # The contract multiplier, so the page can turn a premium move into
        # rupees without hard-coding a lot size that Zerodha revises.
        "lot_size": meta.get("lot_size"),
        "max_lots": getattr(config, "MAX_LOTS", 5),
        "expiry": (rec.get("option_chain") or {}).get("expiry"),
        "risk_points": rec.get("risk_points"),
        "reach_points": rec.get("reach_points"),
        "reach_to_risk": rec.get("reach_to_risk"),
        "reach_reason": rec.get("reach_reason"),
        "not_worth_it": rec.get("not_worth_it"),
        "adx_blocked": rec.get("adx_blocked"),
        "blockers": rec.get("blockers") or [],
        "votes": rec.get("votes") or {},
        "agree": rec.get("agree"), "dissent": rec.get("dissent"),
        "adx": tech.get("adx"), "adx_ok": tech.get("adx_ok"),
        "rsi": round(tech["last_rsi"], 1) if tech.get("last_rsi") is not None else None,
        "macd_hist": tech.get("macd_hist"),
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
        self.sug_tokens = {}      # index name -> (strike, type, token) suggested
        self.sug_px = {}          # index name -> that contract's live premium
        self.base_df = {}         # index name -> the completed REST candles
        self.base_oi = {}         # index name -> the last option-chain snapshot
        self._last_live = 0.0     # when the live recompute last ran
        self.eq_tokens = {}       # tradingsymbol -> token, for the heat map
        self._eq_tried = False
        self._idx_tried = 0.0     # when the index tokens were last looked up
        self._crypto = None       # built on demand, only if crypto is enabled
        self.ticker = None        # the fast loop that reads it
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

    def instruments(self):
        """Only this feed's market. A crypto feed must never reach for NIFTY."""
        return config.instruments_in(self.market)

    def _idle(self):
        return time.time() - self.last_touch > IDLE_SECONDS

    def _set(self, **fields):
        with self.lock:
            self.state.update(fields)

    # -- the loop -----------------------------------------------------------
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
                open_now = is_market_open(now_ist())
                for name in self.instruments():
                    if _stopping.is_set() or self._idle():
                        break
                    try:
                        self.stage = f"analyse:{name}"
                        rec, notes = fetch_recommendation(
                            self._provider_for(name, provider),
                            name, "15m", None, quiet=True,
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
        self.stream_error = None
        self._subscribe_indices()
        self.ticker = threading.Thread(target=self._tick_loop, daemon=True,
                                       name=f"ticks:{self.key}")
        self.ticker.start()

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

    def _subscribe_ticket(self, name):
        """Add an open ticket's own contract to the feed.

        The index token is not enough: a ticket is tracked on the premium of
        one specific strike, and that contract has its own price, which is the
        one the P&L is actually made of.
        """
        if self.streamer is None or self.opt_tokens.get(name) is not None:
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
        st, tok = self.streamer, self.tokens.get(name)
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
            except Exception:
                continue

            try:
                evs = self.tickets.update(name, rec)
            except Exception:
                evs = []
            with self.lock:
                entry = self.state["indices"].get(name)
                if entry is None:
                    continue
                entry["rec"] = rec
                entry["public"] = _public(rec, name)
                entry["why"] = explain.explain(rec)
                entry["at"] = now_ist().strftime("%H:%M:%S")
                for ev in evs:
                    ev["at"] = entry["at"]
                    self.events.insert(0, ev)
                del self.events[30:]

    def forming(self):
        """The in-progress candle per index, so the chart's last bar can move
        instead of waiting for the next fetch."""
        st = self.streamer
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
        while not _stopping.is_set() and not self._idle():
            st = self.streamer
            if st is None:
                return
            try:
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
            except Exception:
                pass
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
                            and is_market_open(now_ist())),
               "age": round(age, 1) if age is not None else None,
               "premium": {}, "ltp": sug, "bar": self.forming(),
               "stream_error": self.stream_error, "stage": self.stage}
        if st is not None:
            for name, tok in list(self.opt_tokens.items()):
                px = st.price(tok)
                if px is not None:
                    out["premium"][name] = px
        return out

    def stop_stream(self):
        st, self.streamer = self.streamer, None
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _sleep(self, seconds):
        """Wait, but come back early if we're stopping or something woke us."""
        if _stopping.wait(0):
            return
        if self.wake.wait(seconds):
            self.wake.clear()

    # -- reading ------------------------------------------------------------
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
                "tickets": {k: self.tickets.public(k) for k in self.state["indices"]},
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
