"""
data_providers.py
-------------------
Two interchangeable data sources:

  FreeDataProvider  - price via Yahoo Finance's public chart endpoint
                       (no key needed), option chain via NSE's public
                       (unofficial) option-chain API for NIFTY/BANKNIFTY.
                       SENSEX has no free public option-chain source, so
                       only technical (price-based) signals are available
                       for it in free mode.

  KiteDataProvider  - price + option chain via your own Zerodha Kite
                       Connect subscription (real-time ticks, real OI).
                       Requires KITE_API_KEY + KITE_ACCESS_TOKEN.

Both expose the same interface:
  get_ohlc(index_key, interval, lookback_days) -> pandas.DataFrame
      columns: Open, High, Low, Close, Volume ; DatetimeIndex

  get_option_chain(index_key) -> dict or None
      {
        "expiry": "YYYY-MM-DD",
        "spot": float,
        "strikes": [ {strike, call_oi, put_oi, call_ltp, put_ltp}, ... ],
        "pcr": float,
        "max_pain": float,
        "top_call_oi_strike": float,   # nearest resistance
        "top_put_oi_strike": float,    # nearest support
      }
      Returns None if unavailable (e.g. SENSEX in free mode, or fetch failed).
"""

import time
import threading
import datetime as dt
from typing import Optional

import requests
import pandas as pd

# ---------------------------------------------------------------------------
# NSE/BSE trade on India Standard Time (IST), no matter where this tool is
# actually run from. Kite Connect's historical_data() interprets the
# from/to datetimes we pass it as IST wall-clock time — so if we naively
# used dt.datetime.now() here, a machine set to any other timezone (US
# Eastern, UK, etc.) would silently ask Kite for data "up to now" using the
# WRONG "now", often landing outside market hours entirely and making the
# fetched price look frozen/stale. _now_ist_naive() always converts first.
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _now_ist_naive() -> dt.datetime:
    """Current India time, correct regardless of this machine's own
    timezone, returned as a naive datetime (no tzinfo) since that's the
    format Kite Connect's historical_data() expects."""
    return dt.datetime.now(_IST).replace(tzinfo=None)

from config import INSTRUMENTS


# =============================================================================
# FREE DATA PROVIDER
# =============================================================================
class FreeDataProvider:
    YAHOO_INTERVAL_MAP = {
        "5m": ("5m", "5d"),
        "15m": ("15m", "1mo"),
        "1d": ("1d", "1y"),
    }

    def __init__(self):
        self._nse_session = None

    def refresh_session(self):
        """Force a fresh NSE session + cookies on the next option-chain call.
        Useful in long-running --live sessions where cookies can go stale."""
        self._nse_session = None

    # ---------------------------- PRICE ------------------------------------
    def get_ohlc(self, index_key: str, interval: str = "15m", lookback_days: Optional[int] = None) -> pd.DataFrame:
        ticker = INSTRUMENTS[index_key]["yahoo_ticker"]
        if interval not in self.YAHOO_INTERVAL_MAP:
            # Same reasoning as the Kite provider: an unknown interval used to
            # be passed straight through to Yahoo, which rejected it with a
            # raw HTTP error rather than saying what was actually wrong.
            raise ValueError(
                f"unknown interval {interval!r}; expected one of "
                f"{sorted(self.YAHOO_INTERVAL_MAP)}")
        yf_interval, default_range = self.YAHOO_INTERVAL_MAP[interval]
        rng = f"{lookback_days}d" if lookback_days else default_range

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": yf_interval, "range": rng}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("chart", {}).get("result")
        if not result:
            raise RuntimeError(f"No chart data returned for {ticker}: {data.get('chart', {}).get('error')}")

        result = result[0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        df = pd.DataFrame(
            {
                "Open": quote["open"],
                "High": quote["high"],
                "Low": quote["low"],
                "Close": quote["close"],
                "Volume": quote["volume"],
            },
            index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata"),
        )
        df = df.dropna(subset=["Close"])
        df["Volume"] = df["Volume"].fillna(0)
        return df

    # ---------------------------- OPTION CHAIN ------------------------------
    def _get_nse_session(self) -> requests.Session:
        if self._nse_session is not None:
            return self._nse_session

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        # NSE requires a "warm up" hit on the homepage first to receive
        # cookies before the API will respond (basic anti-scraping check).
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(0.5)
        session.get("https://www.nseindia.com/option-chain", timeout=10)
        self._nse_session = session
        return session

    def _fetch_nse_option_chain_raw(self, index_key: str):
        """Hits the NSE endpoint once and returns the raw JSON (or None on
        failure). Cached per call site so get_expiry_dates() and
        get_option_chain() can share one fetch when called back-to-back."""
        meta = INSTRUMENTS[index_key]
        if not meta["has_free_option_chain"]:
            return None
        symbol = meta["nse_symbol"]
        url = "https://www.nseindia.com/api/option-chain-indices"
        try:
            session = self._get_nse_session()
            resp = session.get(url, params={"symbol": symbol}, timeout=15)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    def get_expiry_dates(self, index_key: str) -> list:
        """Returns all available expiry date strings (nearest first) for
        this index, e.g. ['14-Aug-2026', '21-Aug-2026', ...]. Empty list if
        unavailable (e.g. Sensex in free mode, or the fetch failed)."""
        data = self._fetch_nse_option_chain_raw(index_key)
        if not data:
            return []
        return data.get("records", {}).get("expiryDates", [])

    def get_option_chain(self, index_key: str, expiry: str = None):
        """`expiry` — one of the strings returned by get_expiry_dates(); if
        None (default), uses the nearest expiry."""
        data = self._fetch_nse_option_chain_raw(index_key)
        if not data:
            return None

        records = data.get("records", {})
        spot = records.get("underlyingValue")
        expiry_dates = records.get("expiryDates", [])
        if not expiry_dates:
            return None
        target_expiry = expiry if expiry in expiry_dates else expiry_dates[0]

        rows = [r for r in records.get("data", []) if r.get("expiryDate") == target_expiry]

        strikes = []
        total_call_oi = 0
        total_put_oi = 0
        for r in rows:
            ce = r.get("CE", {})
            pe = r.get("PE", {})
            call_oi = ce.get("openInterest", 0) or 0
            put_oi = pe.get("openInterest", 0) or 0
            total_call_oi += call_oi
            total_put_oi += put_oi
            strikes.append(
                {
                    "strike": r.get("strikePrice"),
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "call_ltp": ce.get("lastPrice"),
                    "put_ltp": pe.get("lastPrice"),
                }
            )

        if not strikes:
            return None

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi else None
        top_call = max(strikes, key=lambda x: x["call_oi"])
        top_put = max(strikes, key=lambda x: x["put_oi"])
        max_pain = _compute_max_pain(strikes)

        return {
            "expiry": target_expiry,
            "spot": spot,
            "strikes": strikes,
            "pcr": pcr,
            "max_pain": max_pain,
            "top_call_oi_strike": top_call["strike"],
            "top_put_oi_strike": top_put["strike"],
        }


def _compute_max_pain(strikes: list) -> Optional[float]:
    """Classic max-pain calc: for each candidate expiry strike, total option
    writers' payout if index settles there; max pain = strike with min payout."""
    candidate_strikes = [s["strike"] for s in strikes]
    best_strike, best_pain = None, None
    for candidate in candidate_strikes:
        pain = 0
        for s in strikes:
            if candidate > s["strike"]:
                pain += (candidate - s["strike"]) * s["call_oi"]
            if candidate < s["strike"]:
                pain += (s["strike"] - candidate) * s["put_oi"]
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, candidate
    return best_strike


# =============================================================================
# KITE CONNECT DATA PROVIDER (real-time, requires paid API subscription)
# =============================================================================
class KiteDataProvider:
    KITE_INTERVAL_MAP = {"5m": "5minute", "15m": "15minute", "1d": "day"}

    def __init__(self, api_key: str, access_token: str):
        try:
            from kiteconnect import KiteConnect
        except ImportError as e:
            raise ImportError(
                "kiteconnect package not installed. Run: pip install kiteconnect"
            ) from e

        self.api_key = api_key
        self.access_token = access_token
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._instrument_cache = {}
        self._option_inst_cache = {}
        self._equity_token_cache = None

    def refresh_session(self):
        """No-op for Kite — the access token is set once per day via
        kite_login_helper.py and can't be silently refreshed mid-session."""
        pass

    def _instrument_token(self, index_key: str) -> int:
        if index_key in self._instrument_cache:
            return self._instrument_cache[index_key]

        meta = INSTRUMENTS[index_key]
        # NSE/BSE indices live under exchange "NSE"/"BSE" with segment INDICES
        instruments = self.kite.instruments(meta["kite_exchange"])
        for inst in instruments:
            if inst["tradingsymbol"] == meta["kite_tradingsymbol"] and inst["segment"] in (
                "INDICES",
                "BSE-INDICES",
            ):
                self._instrument_cache[index_key] = inst["instrument_token"]
                return inst["instrument_token"]
        raise RuntimeError(f"Could not find instrument token for {index_key}")

    def get_ohlc(self, index_key: str, interval: str = "15m", lookback_days: Optional[int] = None) -> pd.DataFrame:
        token = self._instrument_token(index_key)
        if interval not in self.KITE_INTERVAL_MAP:
            # Refusing beats defaulting. A caller that asked for "5minute"
            # instead of "5m" used to get 15-minute candles back with no error
            # and no warning — an entire strategy ran on the wrong timeframe
            # while every label on screen said otherwise.
            raise ValueError(
                f"unknown interval {interval!r}; expected one of "
                f"{sorted(self.KITE_INTERVAL_MAP)}")
        kite_interval = self.KITE_INTERVAL_MAP[interval]
        days = lookback_days or (5 if interval != "1d" else 250)
        to_date = _now_ist_naive()
        from_date = to_date - dt.timedelta(days=days)

        candles = self.kite.historical_data(token, from_date, to_date, kite_interval)
        df = pd.DataFrame(candles)
        if df.empty:
            raise RuntimeError(f"No historical data returned for {index_key}")
        df = df.rename(
            columns={"date": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )
        df = df.set_index("Date")
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def index_token(self, index_key: str):
        """Public accessor for the index's instrument token — needed to
        subscribe to it on the live WebSocket feed."""
        try:
            return self._instrument_token(index_key)
        except Exception:
            return None

    def equity_tokens(self, symbols):
        """tradingsymbol -> instrument_token for NSE equities, used by the
        market heat map to stream every index constituent at once."""
        wanted = set(symbols)
        if self._equity_token_cache is None:
            rows = self.kite.instruments("NSE")
            self._equity_token_cache = {
                r["tradingsymbol"]: r["instrument_token"]
                for r in rows if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"
            }
        return {s: t for s, t in self._equity_token_cache.items() if s in wanted}

    def option_token(self, index_key: str, strike, option_type: str, expiry=None):
        """Instrument token for one specific option contract, so the live
        feed can stream that exact strike's price tick by tick."""
        try:
            _, opts = self._all_option_instruments(index_key)
        except Exception:
            return None
        cands = [i for i in opts
                 if int(i.get("strike") or 0) == int(strike)
                 and i.get("instrument_type") == option_type]
        if not cands:
            return None
        if expiry:
            exact = [i for i in cands if str(i.get("expiry")) == str(expiry)]
            if exact:
                cands = exact
        cands.sort(key=lambda i: str(i.get("expiry")))
        return cands[0].get("instrument_token")

    def _all_option_instruments(self, index_key: str):
        meta = INSTRUMENTS[index_key]
        exchange = "BFO" if meta["kite_exchange"] == "BSE" else "NFO"
        underlying_prefix = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "SENSEX": "SENSEX"}[index_key]
        # The full instrument dump is a multi-megabyte download and the list
        # only changes when contracts are added — cache it for the session
        # rather than re-fetching on every option-token lookup.
        cached = self._option_inst_cache.get(index_key)
        if cached is not None:
            return exchange, cached
        instruments = self.kite.instruments(exchange)
        filtered = [
            i
            for i in instruments
            if i["name"] == underlying_prefix and i["instrument_type"] in ("CE", "PE")
        ]
        self._option_inst_cache[index_key] = filtered
        return exchange, filtered

    def get_expiry_dates(self, index_key: str) -> list:
        """Returns all available expiry dates (as ISO strings, nearest
        first) for this index from the live Kite instruments list."""
        _, opts = self._all_option_instruments(index_key)
        if not opts:
            return []
        dates = sorted({i["expiry"] for i in opts})
        return [str(d) for d in dates]

    def get_option_chain(self, index_key: str, expiry: str = None):
        """`expiry` — one of the ISO date strings from get_expiry_dates();
        if None (default), uses the nearest expiry."""
        meta = INSTRUMENTS[index_key]
        exchange, opts = self._all_option_instruments(index_key)
        if not opts:
            return None

        available_expiries = sorted({i["expiry"] for i in opts})
        if expiry:
            matching = [d for d in available_expiries if str(d) == expiry]
            target_expiry = matching[0] if matching else available_expiries[0]
        else:
            target_expiry = available_expiries[0]
        opts = [i for i in opts if i["expiry"] == target_expiry]

        # Batch quote fetch (Kite allows up to 500 instruments per call)
        symbols = [f"{exchange}:{i['tradingsymbol']}" for i in opts]
        quotes = {}
        for i in range(0, len(symbols), 200):
            batch = symbols[i : i + 200]
            quotes.update(self.kite.quote(batch))

        by_strike = {}
        for inst in opts:
            key = f"{exchange}:{inst['tradingsymbol']}"
            q = quotes.get(key, {})
            oi = q.get("oi", 0) or 0
            ltp = q.get("last_price")
            strike = inst["strike"]
            entry = by_strike.setdefault(strike, {"strike": strike, "call_oi": 0, "put_oi": 0, "call_ltp": None, "put_ltp": None})
            if inst["instrument_type"] == "CE":
                entry["call_oi"] = oi
                entry["call_ltp"] = ltp
            else:
                entry["put_oi"] = oi
                entry["put_ltp"] = ltp

        strikes = sorted(by_strike.values(), key=lambda x: x["strike"])
        total_call_oi = sum(s["call_oi"] for s in strikes)
        total_put_oi = sum(s["put_oi"] for s in strikes)
        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi else None
        top_call = max(strikes, key=lambda x: x["call_oi"])
        top_put = max(strikes, key=lambda x: x["put_oi"])
        max_pain = _compute_max_pain(strikes)

        ltp_data = self.kite.ltp([f"{meta['kite_exchange']}:{meta['kite_tradingsymbol']}"])
        spot = list(ltp_data.values())[0]["last_price"] if ltp_data else None

        return {
            "expiry": str(target_expiry),
            "spot": spot,
            "strikes": strikes,
            "pcr": pcr,
            "max_pain": max_pain,
            "top_call_oi_strike": top_call["strike"],
            "top_put_oi_strike": top_put["strike"],
        }


# =============================================================================
# LIVE STREAMING (Kite WebSocket)
# =============================================================================
class KiteStreamer:
    """Real-time price feed over Zerodha's WebSocket — the same mechanism the
    Kite app itself uses.

    Polling a REST endpoint every N seconds can only ever show you a snapshot
    that is up to N seconds stale, and hammering it faster gets you
    rate-limited. A WebSocket instead PUSHES every tick as the exchange
    publishes it, so prices move continuously and cost nothing extra.

    Ticks arrive on the library's own background thread and are stored in a
    plain dict under a lock. The GUI just reads that dict a few times a
    second — a local memory read, no network — so the numbers on screen
    update smoothly without any polling at all.

    Only prices stream. Indicators are computed from 15-minute candles and
    the option chain comes from REST, so those still refresh on a slower
    timer; they genuinely cannot change faster than their underlying data.
    """

    def __init__(self, api_key: str, access_token: str, bar_seconds: int = 900):
        self.api_key = api_key
        self.access_token = access_token
        self.bar_seconds = bar_seconds   # 900 = the 15-minute candle
        self._prices = {}          # instrument_token -> last traded price
        self._bars = {}            # instrument_token -> in-progress candle
        self._change = {}          # instrument_token -> % change vs prev close
        self._lock = threading.Lock()
        self._kws = None
        self._subscribed = set()
        self._quote_tokens = set()
        self.connected = False
        self.last_error = None
        self.last_tick_at = None   # time.time() of the most recent tick
        self.tick_count = 0

    # ------------------------------------------------------------------
    def start(self):
        """Open the socket. Safe to call once; reconnects are handled by the
        kiteconnect library itself."""
        try:
            from kiteconnect import KiteTicker
        except ImportError as e:
            self.last_error = f"kiteconnect not installed ({e})"
            return False

        try:
            kws = KiteTicker(self.api_key, self.access_token)
        except Exception as e:
            self.last_error = f"could not create ticker: {e}"
            return False

        def on_ticks(ws, ticks):
            now = time.time()
            # Which 15-minute candle are we inside? IST is offset from UTC by
            # exactly 22 whole 900-second blocks, so bucketing on plain epoch
            # time lands on the same boundaries the exchange uses (09:15,
            # 09:30, 09:45 ...) with no timezone arithmetic needed.
            bucket = int(now // self.bar_seconds) * self.bar_seconds
            with self._lock:
                for t in ticks:
                    tok = t.get("instrument_token")
                    lp = t.get("last_price")
                    if tok is None or lp is None:
                        continue
                    tok = int(tok)
                    lp = float(lp)
                    self._prices[tok] = lp
                    # Quote/full mode carries the day's % change directly.
                    # If only LTP mode is active, derive it from the previous
                    # close in the tick's OHLC block when that's present.
                    ch = t.get("change")
                    if ch is None:
                        prev = (t.get("ohlc") or {}).get("close")
                        if prev:
                            try:
                                ch = (lp - float(prev)) / float(prev) * 100.0
                            except (TypeError, ZeroDivisionError):
                                ch = None
                    if ch is not None:
                        self._change[tok] = float(ch)
                    # Build the in-progress candle from the ticks themselves,
                    # so indicators can be recomputed live instead of waiting
                    # for the candle to close and be fetched over REST.
                    bar = self._bars.get(tok)
                    if bar is None or bar["start"] != bucket:
                        self._bars[tok] = {"start": bucket, "o": lp, "h": lp, "l": lp, "c": lp}
                    else:
                        if lp > bar["h"]:
                            bar["h"] = lp
                        if lp < bar["l"]:
                            bar["l"] = lp
                        bar["c"] = lp
                self.last_tick_at = now
                self.tick_count += len(ticks)

        def on_connect(ws, response):
            self.connected = True
            self.last_error = None
            with self._lock:
                toks = list(self._subscribed)
                quote_toks = [t for t in toks if t in self._quote_tokens]
                ltp_toks = [t for t in toks if t not in self._quote_tokens]
            if toks:
                try:
                    ws.subscribe(toks)
                    if ltp_toks:
                        ws.set_mode(ws.MODE_LTP, ltp_toks)
                    if quote_toks:
                        ws.set_mode(ws.MODE_QUOTE, quote_toks)
                except Exception as e:
                    self.last_error = f"subscribe failed: {e}"

        def on_close(ws, code, reason):
            self.connected = False

        def on_error(ws, code, reason):
            self.connected = False
            self.last_error = f"{code}: {reason}"

        kws.on_ticks = on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        kws.on_error = on_error

        self._kws = kws
        try:
            # threaded=True keeps the socket on its own thread so it never
            # blocks the window.
            kws.connect(threaded=True)
            return True
        except Exception as e:
            self.last_error = f"connect failed: {e}"
            return False

    # ------------------------------------------------------------------
    def subscribe(self, tokens, quote: bool = False):
        """Add instrument tokens to the feed.

        quote=True asks for QUOTE mode, which includes the day's % change and
        OHLC — needed for the market heat map. LTP mode is lighter and is all
        the index/option tracking needs."""
        tokens = [int(t) for t in tokens if t]
        if not tokens:
            return
        with self._lock:
            fresh = [t for t in tokens if t not in self._subscribed]
            self._subscribed.update(tokens)
            if quote:
                self._quote_tokens.update(tokens)
        # Always try the wire, rather than only when self.connected is already
        # True. connect(threaded=True) returns before the socket is open, so a
        # caller subscribing in that window used to be skipped here and left
        # entirely to on_connect's replay - and if on_connect had just read
        # _subscribed while it was still empty, nothing subscribed the tokens
        # at all, silently and for good. Failing here is safe: the tokens stay
        # in _subscribed and the next on_connect replays them.
        if fresh and self._kws is not None:
            try:
                self._kws.subscribe(fresh)
                mode = self._kws.MODE_QUOTE if quote else self._kws.MODE_LTP
                self._kws.set_mode(mode, fresh)
            except Exception as e:
                self.last_error = f"subscribe failed: {e}"

    def price(self, token):
        """Latest streamed price for a token, or None if no tick yet."""
        if token is None:
            return None
        with self._lock:
            return self._prices.get(int(token))

    def change_pct(self, token):
        """Day's % change for a token, or None if not seen yet."""
        if token is None:
            return None
        with self._lock:
            return self._change.get(int(token))

    def forming_bar(self, token):
        """The candle currently being built from live ticks:
        {start (epoch), o, h, l, c}. None until a tick has arrived.

        Appending this to the completed candles fetched over REST is what
        lets EMA/MACD/RSI/ADX/VWAP move continuously — the same way a
        charting platform updates an indicator before the bar closes."""
        if token is None:
            return None
        with self._lock:
            bar = self._bars.get(int(token))
            return dict(bar) if bar else None

    def age_seconds(self):
        """How long since the last tick — used to show whether the feed is
        genuinely live or has quietly gone stale."""
        if self.last_tick_at is None:
            return None
        return time.time() - self.last_tick_at

    def stop(self):
        self.connected = False
        try:
            if self._kws is not None:
                self._kws.close()
        except Exception:
            pass
        self._kws = None
