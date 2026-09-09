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

import threading
import time
import traceback

import config
import explain
import tickets
import user_kite
from data_providers import FreeDataProvider, KiteDataProvider
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


def configure(mode, interval, expiry=None):
    _settings.update(mode=mode, interval=interval, expiry=expiry)


def _key_for(email):
    return SHARED if _settings["mode"] == "free" else (email or "").strip().lower()


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

    def __init__(self, key, email):
        self.key = key
        self.email = email
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
        self.tickets = tickets.TicketBook(email if key != SHARED else None)
        self.events = []          # what just happened, newest first
        self.bell_closed = False  # whether today's tickets were squared up
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

    def _idle(self):
        return time.time() - self.last_touch > IDLE_SECONDS

    def _set(self, **fields):
        with self.lock:
            self.state.update(fields)

    # -- the loop -----------------------------------------------------------
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
                open_now = is_market_open(now_ist())
                for name in config.INSTRUMENTS:
                    if _stopping.is_set() or self._idle():
                        break
                    try:
                        rec, notes = fetch_recommendation(
                            provider, name, "15m", None, quiet=True,
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
                self._refresh_history(provider)
            except Exception:
                self._set(error=traceback.format_exc(limit=2), feed="unknown")
                provider = None
                self._sleep(10)
                continue
            self._sleep(_settings["interval"])
        self._set(feed="idle")
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
        for name in config.INSTRUMENTS:
            if _stopping.is_set() or self._idle():
                return
            try:
                df = provider.get_ohlc(name, interval="15m",
                                       lookback_days=HISTORY_DAYS)
                # Dropped for the same reason the signal drops them: the
                # pre-open auction bar is priced on almost no volume, and a
                # chart that shows it puts a spike on every single morning.
                df = drop_preopen(df)
                if df is not None and len(df) > 1:
                    with self.lock:
                        self.hist[name] = df
            except Exception:
                pass
            time.sleep(0.5)
        self.hist_at = time.time()

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
def for_user(email, start=True):
    """This user's feed, started if it isn't running. None if `start` is off
    and there is nothing running — used by callers that want to read without
    bringing a feed to life, such as the operator page."""
    key = _key_for(email)
    with _lock:
        feed = _feeds.get(key)
        if feed is None:
            if not start:
                return None
            feed = _feeds[key] = Feed(key, email)
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
