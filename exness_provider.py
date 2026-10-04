"""
exness_provider.py — Exness (MetaTrader 5) prices for BTCUSD and XAUUSD, via MetaApi
================================================================================
The user, 3 Oct 2026: "i want exness" - BTC and gold from Exness, all their data
from Exness, anything Exness does not have removed. Exness only speaks MetaTrader 5;
the server is Linux, so the bridge is MetaApi's cloud REST API (the user's choice),
connected to an Exness DEMO account first (also the user's choice).

WHAT EXNESS HAS, AND DOES NOT
    BTCUSD and XAUUSD are CFDs: a bid, an ask (the spread is the cost) and candles.
    There are NO options - no chain, no expiries, no strikes, no greeks, no open
    interest - so every option method here answers "none" and the page drops those
    tabs for this market. There is no public trade tape either (taker flow is off).

CREDENTIALS - the user's own, never in code or chat
    METAAPI_TOKEN, METAAPI_ACCOUNT_ID, METAAPI_REGION (e.g. "new-york") come from the
    environment (the server's .env). The token rides in the documented `auth-token`
    header and is never logged or put into a URL or an error message.

ENDPOINTS - from MetaApi's own documentation, not guessed
    price    GET https://mt-client-api-v1.<region>.agiliumtrade.ai
                 /users/current/accounts/<id>/symbols/<symbol>/current-price
                 -> {symbol, bid, ask, time, brokerTime, ...}
    symbols  GET https://mt-client-api-v1.<region>.agiliumtrade.ai
                 /users/current/accounts/<id>/symbols -> ["EURUSD", ...]
    candles  GET https://mt-market-data-client-api-v1.<region>.agiliumtrade.ai
                 /users/current/accounts/<id>/historical-market-data/symbols/<symbol>
                 /timeframes/15m/candles?startTime=<ISO>&limit=<=1000
                 (backwards from startTime) -> [{time, open, high, low, close, tickVolume, spread, ...}]

SYMBOL NAMES depend on the Exness account type (Exness's own help pages): Standard
adds "m" (BTCUSDm), Standard Cent "c", Pro none, Raw Spread none or "r". The real
name is read from the account's own symbol list, so any account type works;
EXNESS_SYMBOL_SUFFIX in the environment overrides it.

RATE LIMITS (MetaApi): 5,000 credits per account per 10 s; a price read costs 50.
Every user of this tool shares ONE MetaApi account, so live prices come from ONE
shared poller (both symbols every POLL_S seconds, ~10% of the budget) and candles
are cached for CANDLE_CACHE_S - never one poll per viewer. A 429 backs off for the
time MetaApi recommends.
"""
import datetime as dt
import os
import sys
import threading
import time

import pandas as pd
import requests

import config

CLIENT = "https://mt-client-api-v1.{region}.agiliumtrade.ai"
MARKET_DATA = "https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"
# Every second (the user, 4 Oct 2026: "yes make it every second"): Exness's BTC quote changed about once a second
# (19 different quotes in 20 s, measured), so 2 s caught about every other tick. Each pass asks for both symbols
# (~0.3 s a request), and _run() sleeps only what is left of the second (never under 0.2 s).
POLL_S = 1.0
CANDLE_CACHE_S = 30.0
# Just after a candle closes, the held history still lacks the NEW (forming) candle, and the entry rules read only a
# candle they know is closed (cfd_rules.evaluate) - so for up to CANDLE_CACHE_S the BTC card read "waiting" every
# 15 minutes (the user, 4 Oct 2026: "every 15 minutes the page is reloading for rsi extreme"). In the first
# CANDLE_CLOSE_WINDOW_S of a new candle, ask again every CANDLE_CLOSE_REFETCH_S until it is there; past that window
# (a closed market - gold at the weekend - has no new candles for days) the normal cache.
CANDLE_CLOSE_REFETCH_S = 2.0
# A live price is asked for every second (POLL_S); waiting the provider's full timeout (30 s) on one slow answer froze
# every price on the page for up to a minute (BTC then gold, one after the other - the user, 4 Oct 2026: "after 15
# minutes load the tool. stopped moving"). A price older than this is worth nothing anyway: give up and ask again.
QUOTE_TIMEOUT_S = 5.0
SLOW_QUOTE_S = 3.0
CANDLE_CLOSE_WINDOW_S = 600.0


def _utcnow():
    return pd.Timestamp.now(tz="UTC")


_LOGGED = {}


def _log(msg, every_s=60.0):
    """To the server's log (journalctl), each distinct message at most once a minute - MetaApi's refusals used to
    be invisible there."""
    now = time.time()
    if now - _LOGGED.get(msg, 0.0) >= every_s:
        _LOGGED[msg] = now
        print(f"exness_provider: {msg}", file=sys.stderr, flush=True)
SUFFIXES = ("", "m", "c", "r")
IST = "Asia/Kolkata"
# The tool's interval names -> MetaApi's timeframes (MetaApi documents 1m..1mn).
TIMEFRAMES = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}
BAR_S = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
# A quote whose last tick is older than this is a CLOSED market, not a quiet one: gold
# stops from Friday night to Sunday night and pauses daily; Bitcoin trades around the
# clock but Exness can halt it for maintenance. No new ticket opens on such a price.
STALE_QUOTE_S = float(getattr(config, "CFD_STALE_QUOTE_S", 600))


def creds():
    """(token, account_id, region) from the environment, or Nones."""
    return (os.environ.get("METAAPI_TOKEN") or None, os.environ.get("METAAPI_ACCOUNT_ID") or None,
            (os.environ.get("METAAPI_REGION") or "new-york").strip())


def configured():
    t, a, _ = creds()
    return bool(t and a)


class NotConfigured(RuntimeError):
    pass


class ExnessMetaApiProvider:
    """The data side - candles and a quote - plus "none" for everything an
    option venue would have, so the shared feed code can ask it the same
    questions it asks Delta and get honest empty answers."""
    QUOTES_IN_COIN = False

    def __init__(self, session=None, timeout=30):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._symbols = None
        self._resolved = {}
        self._candles = {}                     # (symbol, timeframe) -> (fetched_at, UTC frame, days held)
        # One back-off PER MetaApi service (prices: the client API; candles: the market-data API). It used to be one
        # for both, so a 429 on candles froze the live prices too, and the rule read "no candles: RuntimeError"
        # (the user, 4 Oct 2026: "i always getting runtime error for 15 minutes").
        self._backoff_until = {}
        self._lock = threading.Lock()
        # One lock per (symbol, timeframe): a slow first load of a year of daily candles
        # (~8 s from MetaApi, measured 3 Oct 2026) must not hold up the 15-minute candles
        # the signal is computed on.
        self._fetch_locks = {}

    # ------------------------------------------------------------ plumbing
    def _get(self, base, path, timeout=None, **params):
        token, account, region = creds()
        if not (token and account):
            raise NotConfigured("Exness is not connected yet: METAAPI_TOKEN and METAAPI_ACCOUNT_ID "
                                "are not set on the server.")
        svc = "candles" if base == MARKET_DATA else "prices"
        if time.time() < self._backoff_until.get(base, 0.0):
            raise RuntimeError(f"MetaApi asked us to slow down ({svc}); retrying shortly.")
        url = base.format(region=region) + path.format(account=account)
        r = self.session.get(url, params=params or None, headers={"auth-token": token, "Accept": "application/json"},
                             timeout=timeout or self.timeout)
        if r.status_code == 429:
            wait = 10.0
            try:
                body = r.json()
                at = (body.get("metadata") or {}).get("recommendedRetryTime")
                if at:
                    wait = max(1.0, (pd.Timestamp(at) - pd.Timestamp.now(tz="UTC")).total_seconds())
            except Exception:
                pass
            self._backoff_until[base] = time.time() + min(wait, 120.0)
            _log(f"MetaApi 429 on {svc}: backing off {min(wait, 120.0):.0f}s")
            raise RuntimeError(f"MetaApi rate limit ({svc}); backing off {wait:.0f}s")
        if r.status_code == 401:
            raise RuntimeError("MetaApi refused the token (401) - check METAAPI_TOKEN.")
        if r.status_code == 404:
            raise RuntimeError("MetaApi: account not found / not deployed yet, or the symbol is not "
                               "on this account (404).")
        if r.status_code == 504:
            # MetaApi's own TimeoutError: "not connected to broker yet" (seen 3 Oct 2026 while the
            # account was still UNDEPLOYED) - an account to switch on, not a request to retry hard.
            raise RuntimeError("MetaApi: the Exness account is not connected to the broker right now (504) "
                               "- check it is Deployed and Connected at app.metaapi.cloud.")
        r.raise_for_status()
        return r.json()

    def refresh_session(self):
        return None

    # ------------------------------------------------------------ symbols
    def broker_symbol(self, index_key):
        """The account's real name for this instrument (BTCUSD / BTCUSDm / ...)."""
        base = (config.INSTRUMENTS.get(index_key) or {}).get("exness_symbol")
        if not base:
            raise ValueError(f"{index_key} has no exness_symbol")
        if base in self._resolved:
            return self._resolved[base]
        forced = os.environ.get("EXNESS_SYMBOL_SUFFIX")
        if forced is not None and forced.strip() != "":
            self._resolved[base] = base + forced.strip()
            return self._resolved[base]
        if self._symbols is None:
            self._symbols = set(self._get(CLIENT, "/users/current/accounts/{account}/symbols") or [])
        for suf in SUFFIXES:
            if base + suf in self._symbols:
                self._resolved[base] = base + suf
                return self._resolved[base]
        raise RuntimeError(f"{base} is not offered on this Exness account (tried suffixes "
                           f"{', '.join(repr(s) for s in SUFFIXES)}).")

    def index_token(self, index_key):
        return self.broker_symbol(index_key)

    def equity_tokens(self, symbols):
        return {}

    # ------------------------------------------------------------ prices
    def quote(self, index_key):
        sym = self.broker_symbol(index_key)
        q = self._get(CLIENT, "/users/current/accounts/{account}/symbols/" + sym + "/current-price",
                      timeout=QUOTE_TIMEOUT_S, keepSubscription="true")
        bid, ask = float(q["bid"]), float(q["ask"])
        try:
            tick_at = pd.Timestamp(q["time"]).timestamp() if q.get("time") else None
        except (TypeError, ValueError):
            tick_at = None
        return {"symbol": sym, "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
                "time": q.get("time"), "tick_at": tick_at}

    def spot(self, index_key):
        return self.quote(index_key)["mid"]

    @staticmethod
    def _frame(rows):
        """MetaApi candle dicts -> the feed's columns, UTC index, oldest first, one row per time."""
        seen = {pd.Timestamp(c["time"]): c for c in rows}
        ts = sorted(seen)
        df = pd.DataFrame({"Open": [float(seen[t]["open"]) for t in ts],
                           "High": [float(seen[t]["high"]) for t in ts],
                           "Low": [float(seen[t]["low"]) for t in ts],
                           "Close": [float(seen[t]["close"]) for t in ts],
                           "Volume": [float(seen[t].get("tickVolume") or 0.0) for t in ts]},
                          index=pd.DatetimeIndex(ts).tz_convert("UTC"))
        df.index.name = "Date"
        return df

    def _page(self, sym, tf, start, limit):
        return self._get(MARKET_DATA, "/users/current/accounts/{account}/historical-market-data/symbols/"
                         + sym + "/timeframes/" + tf + "/candles",
                         startTime=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), limit=int(limit)) or []

    def get_ohlc(self, index_key, interval="15m", lookback_days=None):
        """Candles, newest last, IST index.

        ONE history per symbol and timeframe, shared by every caller (every user's
        feed asks for the same two symbols): filled the first time by paging
        backwards 1,000 at a time, then kept current by fetching only the bars since
        the last one - so the live loop's every-pass ask costs one small request, at
        most once per CANDLE_CACHE_S, instead of re-reading weeks of candles."""
        tf = TIMEFRAMES.get(interval)
        if tf is None:
            raise ValueError(f"unknown interval {interval!r}; expected one of {sorted(TIMEFRAMES)}")
        days = lookback_days or 5
        sym = self.broker_symbol(index_key)
        key = (sym, tf)
        bar = pd.Timedelta(seconds=BAR_S[interval])
        with self._lock:
            fetch_lock = self._fetch_locks.setdefault(key, threading.Lock())
        with fetch_lock:
            now = _utcnow()
            want_from = now - pd.Timedelta(days=days)
            hit = self._candles.get(key)
            covers = hit is not None and len(hit[1]) and hit[1].index.min() <= want_from + bar
            keep_s = CANDLE_CACHE_S
            if covers:
                since_due = (now - (hit[1].index.max() + bar)).total_seconds()     # the new candle is this late
                if 0 <= since_due < CANDLE_CLOSE_WINDOW_S:
                    keep_s = CANDLE_CLOSE_REFETCH_S
            if covers and time.time() - hit[0] < keep_s:
                df = hit[1]
            elif covers:
                # Topping up: the bars since the newest one held (the newest is
                # refetched too - it was still forming when it was read).
                missing = int((now - hit[1].index.max()) / bar) + 2
                try:
                    fresh = self._frame(self._page(sym, tf, now, min(1000, max(5, missing))) or [])
                except RuntimeError as exc:
                    # A refused / failed top-up keeps the history already held - the rule then reads it (or waits for
                    # the new candle) instead of failing outright; asked again on the next call.
                    _log(f"{sym} {tf} top-up failed, serving the held candles: {exc}")
                    out = hit[1][hit[1].index >= want_from].copy()
                    out.index = out.index.tz_convert(IST)
                    return out
                df = pd.concat([hit[1][~hit[1].index.isin(fresh.index)], fresh]).sort_index()
                df = df[df.index >= now - pd.Timedelta(days=max(days, hit[2]))]
                self._candles[key] = (time.time(), df, max(days, hit[2]))
            else:
                # Only as many bars as the lookback needs - 1,000 daily bars when 250 are
                # wanted took MetaApi past the timeout (3 Oct 2026); 255 take ~8 s.
                start, rows = now, []
                need = int(pd.Timedelta(days=days) / bar) + 2
                for _ in range(40):
                    lim = min(1000, max(5, need - len(rows)))
                    page = self._page(sym, tf, start, lim)
                    if not page:
                        break
                    rows += page
                    oldest = min(pd.Timestamp(c["time"]) for c in page)
                    if oldest <= want_from or len(page) < lim:
                        break
                    start = oldest - pd.Timedelta(milliseconds=1)
                if not rows:
                    raise RuntimeError(f"MetaApi returned no candles for {sym}")
                df = self._frame(rows)
                self._candles[key] = (time.time(), df, days)
        out = df[df.index >= want_from].copy()
        out.index = out.index.tz_convert(IST)
        return out

    # ------------------------------------------------------------ no options at Exness
    def get_option_chain(self, index_key, expiry=None):
        return None

    def get_expiry_dates(self, index_key):
        return []

    def pick_expiry(self, index_key):
        return None

    def option_instrument(self, index_key, strike, option_type, expiry=None):
        return None

    def option_token(self, index_key, strike, option_type, expiry=None):
        return None

    def candles_for_token(self, token, interval="day", days=90):
        raise ValueError("Exness has no option contracts")


_SHARED = None
_SHARED_LOCK = threading.Lock()


def shared():
    """The ONE provider every feed uses: one symbol list, one candle history per
    symbol and timeframe, one 429 back-off - one MetaApi account behind them all."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = ExnessMetaApiProvider()
        return _SHARED


# ---------------------------------------------------------------- the shared live poller
class _Poller:
    """ONE thread for the whole server: both symbols every POLL_S seconds."""

    def __init__(self, provider=None, poll_s=POLL_S):
        self.provider = provider or shared()
        self.poll_s = poll_s
        self.keys = set()
        self.quotes = {}                        # broker symbol -> {bid, ask, mid, spread, at}
        self.bars = {}                          # broker symbol -> the 15-minute bar being built
        self.lock = threading.Lock()
        self.started = False
        self.last_error = None
        self.last_tick_at = None

    def watch(self, index_key):
        with self.lock:
            self.keys.add(index_key)
            if not self.started:
                self.started = True
                threading.Thread(target=self._run, daemon=True, name="exness-poller").start()

    def poll_once(self, now=None):
        for k in list(self.keys):
            began = time.time()
            try:
                q = self.provider.quote(k)
            except Exception as exc:
                self.last_error = str(exc)[:200]
                _log(f"{k} price failed after {time.time() - began:.1f}s: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            took = time.time() - began
            if took > SLOW_QUOTE_S:
                _log(f"{k} price slow: {took:.1f}s")
            t = now or time.time()
            # The last tick's own time: a closed market (gold at the weekend) keeps answering
            # with Friday's quote, and that must neither look live nor build flat bars.
            tick_at = q.get("tick_at") or t
            fresh = t - tick_at <= STALE_QUOTE_S
            with self.lock:
                self.quotes[q["symbol"]] = dict(q, at=t, tick_at=tick_at)
                if fresh:
                    start = int(t // 900) * 900
                    b = self.bars.get(q["symbol"])
                    if b is None or b["start"] != start:
                        self.bars[q["symbol"]] = {"start": start, "open": q["mid"], "high": q["mid"],
                                                  "low": q["mid"], "close": q["mid"]}
                    else:
                        b["high"] = max(b["high"], q["mid"])
                        b["low"] = min(b["low"], q["mid"])
                        b["close"] = q["mid"]
                else:
                    self.bars.pop(q["symbol"], None)
            self.last_tick_at = t
            self.last_error = None

    def price_age(self, index_key, now=None):
        """Seconds since this instrument's last real tick, or None before the first quote."""
        try:
            sym = self.provider.broker_symbol(index_key)
        except Exception:
            return None
        with self.lock:
            q = self.quotes.get(sym)
        return None if not q else max(0.0, (now or time.time()) - q["tick_at"])

    def _run(self):
        while True:
            began = time.time()
            self.poll_once()
            time.sleep(max(0.2, self.poll_s - (time.time() - began)))


_POLLER = None
_POLLER_LOCK = threading.Lock()


def poller():
    global _POLLER
    with _POLLER_LOCK:
        if _POLLER is None:
            _POLLER = _Poller()
        return _POLLER


class ExnessStreamer:
    """The same surface DeltaStreamer gives the feed, read from the ONE shared
    poller. Option methods answer None - there are no options here."""
    QUOTES_IN_COIN = False

    def __init__(self, shared=None):
        self._p = shared or poller()
        self.connected = False
        self.last_error = None

    @property
    def last_tick_at(self):
        return self._p.last_tick_at

    def start(self):
        if not configured():
            self.last_error = "Exness is not connected yet (METAAPI_TOKEN / METAAPI_ACCOUNT_ID not set)."
            return False
        self.connected = True
        return True

    def subscribe_index(self, index_symbol):
        # The feed subscribes by config.crypto_index(name), which for Exness is the
        # instrument KEY (BTC / GOLD) - the poller resolves the broker's own name.
        self._p.watch(index_symbol)

    def subscribe(self, subs):
        return None

    def subscribe_ticker(self, instrument):
        return None

    def subscribe_tickers(self, instruments):
        return None

    def subscribe_trades(self, symbol):
        return None

    def tape_for(self, symbol):
        return None

    def _sym(self, key):
        try:
            return self._p.provider.broker_symbol(key)
        except Exception:
            return key

    def index_price(self, index_symbol):
        with self._p.lock:
            q = self._p.quotes.get(self._sym(index_symbol))
        self.last_error = self._p.last_error
        return q["mid"] if q else None

    def quote(self, index_symbol, max_age=None):
        with self._p.lock:
            q = self._p.quotes.get(self._sym(index_symbol))
        if not q or (max_age is not None and time.time() - q["at"] > max_age):
            return None
        return dict(q)

    def mark_usd(self, instrument, index_symbol=None):
        return None

    def book(self, instrument, max_age=None):
        return None

    def forming_bar(self, index_symbol):
        """The bar being built, in the feed's own keys (start, o, h, l, c - what
        DeltaStreamer and KiteStreamer hand feeds._df_with_live_bar)."""
        with self._p.lock:
            b = self._p.bars.get(self._sym(index_symbol))
            b = dict(b) if b else None
        if not b:
            return None
        return dict(b, o=b["open"], h=b["high"], l=b["low"], c=b["close"])

    def price_age(self, index_symbol):
        """Seconds since the instrument's last real tick (STALE_QUOTE_S and over: closed)."""
        return self._p.price_age(index_symbol)

    def age_seconds(self):
        t = self._p.last_tick_at
        return None if t is None else time.time() - t

    def stop(self):
        self.connected = False
