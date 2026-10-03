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
import threading
import time

import pandas as pd
import requests

import config

CLIENT = "https://mt-client-api-v1.{region}.agiliumtrade.ai"
MARKET_DATA = "https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"
POLL_S = 2.0
CANDLE_CACHE_S = 30.0
SUFFIXES = ("", "m", "c", "r")
IST = "Asia/Kolkata"


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

    def __init__(self, session=None, timeout=15):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._symbols = None
        self._resolved = {}
        self._candles = {}                     # (symbol, days) -> (fetched_at, frame)
        self._backoff_until = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    def _get(self, base, path, **params):
        token, account, region = creds()
        if not (token and account):
            raise NotConfigured("Exness is not connected yet: METAAPI_TOKEN and METAAPI_ACCOUNT_ID "
                                "are not set on the server.")
        if time.time() < self._backoff_until:
            raise RuntimeError("MetaApi asked us to slow down; retrying shortly.")
        url = base.format(region=region) + path.format(account=account)
        r = self.session.get(url, params=params or None, headers={"auth-token": token, "Accept": "application/json"},
                             timeout=self.timeout)
        if r.status_code == 429:
            wait = 10.0
            try:
                body = r.json()
                at = (body.get("metadata") or {}).get("recommendedRetryTime")
                if at:
                    wait = max(1.0, (pd.Timestamp(at) - pd.Timestamp.now(tz="UTC")).total_seconds())
            except Exception:
                pass
            self._backoff_until = time.time() + min(wait, 120.0)
            raise RuntimeError(f"MetaApi rate limit; backing off {wait:.0f}s")
        if r.status_code == 401:
            raise RuntimeError("MetaApi refused the token (401) - check METAAPI_TOKEN.")
        if r.status_code == 404:
            raise RuntimeError("MetaApi: account not found / not deployed yet, or the symbol is not "
                               "on this account (404).")
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
                      keepSubscription="true")
        bid, ask = float(q["bid"]), float(q["ask"])
        return {"symbol": sym, "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid,
                "time": q.get("time")}

    def spot(self, index_key):
        return self.quote(index_key)["mid"]

    def get_ohlc(self, index_key, interval="15m", lookback_days=None):
        """15-minute candles, newest last, IST index - paged backwards 1,000 at a
        time from now, cached CANDLE_CACHE_S per symbol and length."""
        if interval != "15m":
            raise ValueError("Exness candles are wired for 15m only")
        days = lookback_days or 5
        sym = self.broker_symbol(index_key)
        key = (sym, days)
        with self._lock:
            hit = self._candles.get(key)
            if hit and time.time() - hit[0] < CANDLE_CACHE_S:
                return hit[1].copy()
        want_from = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        start = pd.Timestamp.now(tz="UTC")
        rows = []
        for _ in range(40):
            page = self._get(MARKET_DATA, "/users/current/accounts/{account}/historical-market-data/symbols/"
                             + sym + "/timeframes/15m/candles",
                             startTime=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), limit=1000) or []
            if not page:
                break
            rows += page
            oldest = min(pd.Timestamp(c["time"]) for c in page)
            if oldest <= want_from or len(page) < 1000:
                break
            start = oldest - pd.Timedelta(milliseconds=1)
        if not rows:
            raise RuntimeError(f"MetaApi returned no candles for {sym}")
        seen = {pd.Timestamp(c["time"]): c for c in rows}
        ts = sorted(t for t in seen if t >= want_from)
        df = pd.DataFrame({"Open": [float(seen[t]["open"]) for t in ts],
                           "High": [float(seen[t]["high"]) for t in ts],
                           "Low": [float(seen[t]["low"]) for t in ts],
                           "Close": [float(seen[t]["close"]) for t in ts],
                           "Volume": [float(seen[t].get("tickVolume") or 0.0) for t in ts]},
                          index=pd.DatetimeIndex(ts).tz_convert(IST))
        df.index.name = "Date"
        with self._lock:
            self._candles[key] = (time.time(), df)
        return df.copy()

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


# ---------------------------------------------------------------- the shared live poller
class _Poller:
    """ONE thread for the whole server: both symbols every POLL_S seconds."""

    def __init__(self, provider=None, poll_s=POLL_S):
        self.provider = provider or ExnessMetaApiProvider()
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
            try:
                q = self.provider.quote(k)
            except Exception as exc:
                self.last_error = str(exc)[:200]
                continue
            t = now or time.time()
            with self.lock:
                self.quotes[q["symbol"]] = dict(q, at=t)
                start = int(t // 900) * 900
                b = self.bars.get(q["symbol"])
                if b is None or b["start"] != start:
                    self.bars[q["symbol"]] = {"start": start, "open": q["mid"], "high": q["mid"],
                                              "low": q["mid"], "close": q["mid"]}
                else:
                    b["high"] = max(b["high"], q["mid"])
                    b["low"] = min(b["low"], q["mid"])
                    b["close"] = q["mid"]
            self.last_tick_at = t
            self.last_error = None

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
        with self._p.lock:
            b = self._p.bars.get(self._sym(index_symbol))
            return dict(b) if b else None

    def age_seconds(self):
        t = self._p.last_tick_at
        return None if t is None else time.time() - t

    def stop(self):
        self.connected = False
