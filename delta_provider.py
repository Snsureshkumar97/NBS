"""
delta_provider.py — Bitcoin from Delta Exchange India: candles, spot, the option chain, and a live socket
================================================================================
Asked for by the user on 20 Sep 2026 ("switch BTC to Delta data"): the Bitcoin
market reads Delta Exchange India's own contracts, so a live order there
mirrors the ticket exactly. The public side needs no key - every endpoint used
here is unauthenticated; the keys in user_delta.py are for orders only.

WHAT DELTA IS, against Deribit (which this replaces for BTC)
  * Prices are in DOLLARS already - an option's mark is USD per 1 BTC of
    underlying, no coin-to-dollar conversion anywhere.
  * One contract is 0.001 BTC, so the premium PER CONTRACT is the quoted price
    x 0.001. The tool's lot_size for BTC is therefore 0.001 and "lots" are
    contracts (see config.INSTRUMENTS["BTC"]).
  * Options settle at 12:00 UTC (17:30 IST) on the expiry day; the contract
    symbol is C-BTC-80000-250926 (kind, asset, strike, DDMMYY).
  * Strikes near the money step 200 or 400, not a fixed 1000.
  * Candles come newest first and are paged with the `end` cursor; the index
    (.DEXBTUSD) has no volume, the perpetual (BTCUSD) does.

Two classes with the same public surface as DeribitDataProvider and
DeribitStreamer, so feeds.py and ai_desk.py do not learn a second venue:
get_ohlc / spot / index_token / equity_tokens / get_expiry_dates / pick_expiry /
option_instrument / get_option_chain, and start / subscribe_index /
subscribe_ticker / index_price / mark_usd / book / forming_bar / age_seconds /
stop. QUOTES_IN_COIN says which way a book's prices read.
"""
import datetime as dt
import json
import re
import threading
import time

import pandas as pd
import requests

import config
import taker_flow
from config import INSTRUMENTS
from data_providers import _WSClient, _compute_max_pain

BASE = "https://api.india.delta.exchange"
WS_HOST, WS_PATH = "socket.india.delta.exchange", "/"
SETTLE_HOUR_UTC = 12
PAGE = 2000                       # candles Delta hands back per call at most
CHAIN_CACHE_S = 30
_OPT = re.compile(r"^([CP])-([A-Z]+)-(\d+)-(\d{2})(\d{2})(\d{2})$")

_PICK, _PICK_AT, _SINCE, _BAD = {}, {}, {}, {}     # asset -> the sticky expiry choice, as in Deribit's


def expiry_of(symbol):
    """C-BTC-80000-250926 -> date(2026, 9, 25), or None."""
    m = _OPT.match(str(symbol or ""))
    if not m:
        return None
    try:
        return dt.date(2000 + int(m.group(6)), int(m.group(5)), int(m.group(4)))
    except ValueError:
        return None


def tag_of(expiry):
    """An ISO date (what tickets and the chain carry) -> DDMMYY for a symbol."""
    d = dt.date.fromisoformat(str(expiry)[:10])
    return f"{d.day:02d}{d.month:02d}{d.year % 100:02d}"


def hours_left(expiry, hour=None):
    """Hours until an expiry settles, at `hour` UTC (Bitcoin 12, gold 16 - see
    config.INSTRUMENTS[..]["settle_hour_utc"]); 12 when not given."""
    d = dt.date.fromisoformat(str(expiry)[:10])
    settle = dt.datetime(d.year, d.month, d.day, SETTLE_HOUR_UTC if hour is None else int(hour), 0,
                         tzinfo=dt.timezone.utc)
    return (settle - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600.0


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


class DeltaDataProvider:
    QUOTES_IN_COIN = False           # marks are dollars per 1 BTC of underlying
    RESOLUTION = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
                  "1h": "1h", "2h": "2h", "1d": "1d"}

    def __init__(self, timeout: int = 15, session=None):
        self.timeout = timeout
        self.session = session or requests
        self._chain_cache = {}

    def _get(self, path, **params):
        r = self.session.get(BASE + path, params=params, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and body.get("success") is False:
            raise RuntimeError(f"delta {path}: {(body.get('error') or {}).get('code') or body.get('error')}")
        return body.get("result", body) if isinstance(body, dict) else body

    def refresh_session(self):
        return None

    # ------------------------------------------------------------ candles
    def get_ohlc(self, index_key: str, interval: str = "15m", lookback_days=None) -> pd.DataFrame:
        meta = INSTRUMENTS[index_key]
        if interval not in self.RESOLUTION:
            raise ValueError(f"unknown interval {interval!r}; expected one of {sorted(self.RESOLUTION)}")
        days = lookback_days or 5
        end = int(time.time())
        start = end - days * 86400
        rows, cursor = [], end
        for _ in range(40):                             # pages, newest first
            page = self._get("/v2/history/candles", symbol=meta["delta_perpetual"],
                             resolution=self.RESOLUTION[interval], start=start, end=cursor) or []
            rows += page
            if len(page) < PAGE:
                break
            oldest = min(int(x["time"]) for x in page)
            if oldest <= start:
                break
            cursor = oldest - 1
        if not rows:
            raise RuntimeError(f"delta returned no candles for {index_key}")
        seen = {}
        for x in rows:
            seen[int(x["time"])] = x
        ts = sorted(seen)
        idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
        df = pd.DataFrame({"Open": [float(seen[t]["open"]) for t in ts], "High": [float(seen[t]["high"]) for t in ts],
                           "Low": [float(seen[t]["low"]) for t in ts], "Close": [float(seen[t]["close"]) for t in ts],
                           "Volume": [float(seen[t].get("volume") or 0.0) for t in ts]}, index=idx)
        df.index.name = "Date"
        return df

    # ------------------------------------------------------------ spot
    def spot(self, index_key: str):
        """The index price over REST. Delta's index ticker (.DEXBTUSD) answers
        with a null result at times (seen 20 Sep 2026), so the perpetual's own
        spot_price - the same index - stands in when it does."""
        meta = INSTRUMENTS[index_key]
        px = None
        try:
            t = self._get(f"/v2/tickers/{meta['delta_index']}") or {}
            px = _f(t.get("spot_price")) or _f(t.get("close")) or _f(t.get("mark_price"))
        except Exception:
            px = None
        if px is None:
            t = self._get(f"/v2/tickers/{meta['delta_perpetual']}") or {}
            px = _f(t.get("spot_price"))
        if px is None:
            raise RuntimeError(f"delta gave no index price for {index_key}")
        return px

    def index_token(self, index_key: str):
        return INSTRUMENTS[index_key].get("delta_index")

    def equity_tokens(self, symbols):
        return {}

    # ------------------------------------------------------------ the chain
    def _chain_rows(self, index_key: str):
        asset = INSTRUMENTS[index_key]["delta_asset"]
        hit = self._chain_cache.get(asset)
        if hit and time.time() - hit[0] < CHAIN_CACHE_S:
            return hit[1]
        rows = self._get("/v2/tickers", contract_types="call_options,put_options",
                         underlying_asset_symbols=asset) or []
        parsed = []
        for x in rows:
            sym = x.get("symbol") or ""
            m = _OPT.match(sym)
            exp = expiry_of(sym)
            if not m or exp is None or m.group(2) != asset:
                continue
            q = x.get("quotes") or {}
            g = x.get("greeks") or {}
            iv = _f(q.get("mark_iv"))
            parsed.append({"symbol": sym, "expiry": exp.isoformat(), "strike": int(m.group(3)), "kind": m.group(1),
                           "oi": _f(x.get("oi")) or 0.0, "mark": _f(x.get("mark_price")),
                           "last": _f(x.get("close")), "bid": _f(q.get("best_bid")), "ask": _f(q.get("best_ask")),
                           "spot": _f(x.get("spot_price")),
                           # The venue's own greeks and mark implied volatility (a
                           # fraction on the wire), for the Greeks tab - no model here.
                           "iv": round(iv * 100, 2) if iv is not None else None,
                           "delta": _f(g.get("delta")), "gamma": _f(g.get("gamma")),
                           "theta": _f(g.get("theta")), "vega": _f(g.get("vega")), "rho": _f(g.get("rho"))})
        self._chain_cache[asset] = (time.time(), parsed)
        return parsed

    def get_expiry_dates(self, index_key: str) -> list:
        return sorted({r["expiry"] for r in self._chain_rows(index_key)})

    def _atm_spread(self, rows, expiry, spot):
        """Median bid-ask spread, % of mid, of the three strikes nearest the money."""
        mine = [r for r in rows if r["expiry"] == expiry]
        strikes = sorted({r["strike"] for r in mine}, key=lambda k: abs(k - spot))[:3]
        pcts = []
        for r in mine:
            if r["strike"] in strikes and r.get("bid") and r.get("ask") and r["ask"] >= r["bid"]:
                pcts.append((r["ask"] - r["bid"]) / ((r["ask"] + r["bid"]) / 2) * 100)
        if len(pcts) < 3:
            return None
        pcts.sort()
        return pcts[len(pcts) // 2]

    def pick_expiry(self, index_key: str):
        """The expiry the chain, the stream and a new ticket all use: the
        nearest with more than a day to run whose at-the-money spread is under
        MAX_SPREAD_PCT - sticky once chosen, exactly as the Deribit provider."""
        rows = self._chain_rows(index_key)
        if not rows:
            return None
        tags = sorted({r["expiry"] for r in rows})
        cap = config.max_spread_pct(index_key)          # gold has its own, wider limit
        if not cap:
            return tags[0]
        asset = INSTRUMENTS[index_key]["delta_asset"]
        hour = INSTRUMENTS[index_key].get("settle_hour_utc")
        hit = _PICK_AT.get(asset)
        if hit and time.time() - hit < CHAIN_CACHE_S and _PICK.get(asset) in tags:
            return _PICK[asset]
        _PICK_AT[asset] = time.time()
        spot = next((r["spot"] for r in rows if r.get("spot")), None)
        if spot is None:
            try:
                spot = self.spot(index_key)
            except Exception:
                return _PICK.get(asset) or tags[0]
        now = time.time()
        live = [t for t in tags if hours_left(t, hour) > 24]
        prev = _PICK.get(asset)
        if prev in live:
            sp = self._atm_spread(rows, prev, spot)
            if sp is None or sp > cap * 1.5:
                _BAD[asset] = _BAD.get(asset, 0) + 1
                if _BAD[asset] < 4:
                    return prev
            else:
                _BAD[asset] = 0
                if now - _SINCE.get(asset, now) >= 2 * 3600:
                    for t in live[:live.index(prev)]:
                        nsp = self._atm_spread(rows, t, spot)
                        if nsp is not None and nsp <= cap * 0.75:
                            return self._set_pick(asset, t)
                return prev
        for t in live[:8]:
            sp = self._atm_spread(rows, t, spot)
            if sp is not None and sp <= cap:
                return self._set_pick(asset, t)
        return self._set_pick(asset, live[0] if live else tags[0])

    def _set_pick(self, asset, tag):
        if _PICK.get(asset) != tag:
            _SINCE[asset] = time.time()
        _PICK[asset] = tag
        _BAD[asset] = 0
        return tag

    def option_instrument(self, index_key: str, strike, option_type: str, expiry: str = None):
        """Delta's symbol for one contract, e.g. C-BTC-80000-250926, or None if
        it is not listed. CE/PE from the engine; C/P on Delta."""
        rows = self._chain_rows(index_key)
        if not rows:
            return None
        exp = str(expiry)[:10] if expiry else self.pick_expiry(index_key)
        kind = "C" if str(option_type).upper().startswith("C") else "P"
        want = int(round(float(strike)))
        for r in rows:
            if r["expiry"] == exp and r["strike"] == want and r["kind"] == kind:
                return r["symbol"]
        return None

    def option_token(self, index_key: str, strike, option_type: str, expiry=None):
        """What the contract chart and the streams key a contract on: Delta has
        no numeric token, the symbol is the token."""
        return self.option_instrument(index_key, strike, option_type, expiry)

    KITE_INTERVALS = {"minute": "1m", "3minute": "3m", "5minute": "5m", "15minute": "15m",
                      "30minute": "30m", "60minute": "1h", "day": "1d"}

    def candles_for_token(self, token, interval="day", days=90):
        """Candles for ONE contract by its symbol, lower-case columns with a
        `ts` column - the shape the contract chart reads from the Kite
        provider. Kite's interval names are accepted so the chart does not
        learn a second vocabulary; an unknown one raises."""
        res = self.KITE_INTERVALS.get(interval) or (interval if interval in self.RESOLUTION.values() else None)
        if res is None:
            raise ValueError(f"unknown interval {interval!r}")
        end = int(time.time())
        start = end - int(days) * 86400
        rows = self._get("/v2/history/candles", symbol=str(token), resolution=res, start=start, end=end) or []
        seen = {int(x["time"]): x for x in rows}
        ts = sorted(seen)
        df = pd.DataFrame({
            "ts": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata"),
            "open": [float(seen[t]["open"]) for t in ts], "high": [float(seen[t]["high"]) for t in ts],
            "low": [float(seen[t]["low"]) for t in ts], "close": [float(seen[t]["close"]) for t in ts],
            "volume": [float(seen[t].get("volume") or 0.0) for t in ts]})
        return df

    def get_option_chain(self, index_key: str, expiry: str = None):
        """The same shape the NSE, Kite and Deribit providers return."""
        try:
            rows = self._chain_rows(index_key)
        except Exception:
            return None
        if not rows:
            return None
        target = str(expiry)[:10] if expiry else self.pick_expiry(index_key)
        spot = next((r["spot"] for r in rows if r.get("spot")), None)
        if spot is None:
            try:
                spot = self.spot(index_key)
            except Exception:
                return None
        by_strike = {}
        for r in rows:
            if r["expiry"] != target:
                continue
            e = by_strike.setdefault(r["strike"], {"strike": r["strike"], "call_oi": 0, "put_oi": 0,
                                                   "call_ltp": None, "put_ltp": None})
            side = "call" if r["kind"] == "C" else "put"
            e[side + "_oi"] = r["oi"]
            e[side + "_ltp"] = r["mark"] if r["mark"] is not None else r["last"]
            e[side + "_bid"], e[side + "_ask"] = r["bid"], r["ask"]
        strikes = sorted(by_strike.values(), key=lambda x: x["strike"])
        if not strikes:
            return None
        tot_c = sum(x["call_oi"] for x in strikes)
        tot_p = sum(x["put_oi"] for x in strikes)
        return {"expiry": target, "spot": spot, "strikes": strikes,
                "pcr": round(tot_p / tot_c, 3) if tot_c else None,
                "max_pain": _compute_max_pain(strikes),
                "top_call_oi_strike": max(strikes, key=lambda x: x["call_oi"])["strike"],
                "top_put_oi_strike": max(strikes, key=lambda x: x["put_oi"])["strike"]}


# =============================================================================
class DeltaStreamer:
    """Delta India's socket: the index every tick, and the marks, quotes and
    open interest of the contracts asked for. Prices arrive in dollars."""
    HOST, PATH = WS_HOST, WS_PATH
    QUOTES_IN_COIN = False

    def __init__(self):
        self._ws = None
        self._lock = threading.Lock()
        self._index = {}          # ".DEXBTUSD" -> price
        self._tick = {}           # symbol -> {mark, bid, ask, last, oi, at}, in USD
        self._bars = {}           # ".DEXBTUSD" -> the 15-minute bar being built
        self._subs = set()        # (channel, symbol)
        self._tapes = {}          # perpetual symbol -> taker_flow.Tape, filled by the all_trades channel
        self._stop = threading.Event()
        self.connected = False
        self.last_error = None
        self.last_tick_at = None

    # ------------------------------------------------------------------
    def start(self):
        try:
            self._open()
        except Exception as exc:
            self.last_error = f"connect failed, retrying: {exc}"
            self._ws = None
            self.connected = False
        threading.Thread(target=self._run, daemon=True, name="delta-ws").start()
        return True

    def _open(self):
        ws = _WSClient(self.HOST, self.PATH)
        ws.connect()
        self._ws = ws
        self.connected = True
        ws.send(json.dumps({"type": "enable_heartbeat"}))
        with self._lock:
            subs = sorted(self._subs)
        if subs:
            self._send_subscribe(subs)

    def _send_subscribe(self, subs):
        by = {}
        for chan, sym in subs:
            by.setdefault(chan, []).append(sym)
        self._ws.send(json.dumps({"type": "subscribe", "payload": {
            "channels": [{"name": c, "symbols": sorted(s)} for c, s in by.items()]}}))

    def subscribe(self, subs):
        """Add (channel, symbol) pairs. Safe before the socket is up: replayed
        on every connect, which is what makes a reconnect self-healing."""
        fresh = []
        with self._lock:
            for pair in subs:
                pair = tuple(pair)
                if pair not in self._subs:
                    self._subs.add(pair)
                    fresh.append(pair)
        if fresh and self.connected and self._ws is not None:
            try:
                self._send_subscribe(fresh)
            except Exception as exc:
                self.last_error = f"subscribe failed: {exc}"

    def subscribe_index(self, index_symbol):
        self.subscribe([("spot_price", index_symbol)])

    def subscribe_ticker(self, instrument):
        self.subscribe([("v2/ticker", instrument)])

    def subscribe_tickers(self, instruments):
        self.subscribe([("v2/ticker", i) for i in instruments])

    def subscribe_trades(self, symbol):
        """Every print of one contract, for taker_flow. The socket answers with
        a snapshot of the latest few dozen and then each new print."""
        with self._lock:
            self._tapes.setdefault(symbol, taker_flow.Tape())
        self.subscribe([("all_trades", symbol)])

    def tape_for(self, symbol):
        with self._lock:
            return self._tapes.get(symbol)

    # ------------------------------------------------------------------
    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if self._ws is None:
                    self._open()
                msg = json.loads(self._ws.recv())
                backoff = 1.0
                self._handle(msg)
            except Exception as exc:
                self.last_error = str(exc)
                self.connected = False
                try:
                    if self._ws:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)

    def _handle(self, msg, now=None):
        """One message off the socket. Returns True when it moved a price."""
        now = now or time.time()
        typ = msg.get("type")
        if typ == "spot_price":
            sym, px = msg.get("symbol"), _f(msg.get("price"))
            if not sym or px is None:
                return False
            bucket = int(now // 900) * 900
            with self._lock:
                self._index[sym] = px
                bar = self._bars.get(sym)
                if bar is None or bar["start"] != bucket:
                    self._bars[sym] = {"start": bucket, "o": px, "h": px, "l": px, "c": px}
                else:
                    bar["h"], bar["l"], bar["c"] = max(bar["h"], px), min(bar["l"], px), px
            self.last_tick_at = now
            return True
        if typ == "v2/ticker":
            return self.on_ticker(msg, now)
        if typ in ("all_trades", "all_trades_snapshot"):
            tape = self.tape_for(msg.get("symbol"))
            if tape is not None:
                if typ == "all_trades_snapshot":
                    tape.add(msg.get("trades") or [], snapshot=True, now=now)
                else:
                    tape.add([msg], now=now)
            return False              # a print is not a price move; last_tick_at keeps meaning the index
        return False

    def on_ticker(self, data, now=None):
        inst, mk = (data or {}).get("symbol"), _f((data or {}).get("mark_price"))
        if not inst or mk is None:
            return False
        q = data.get("quotes") or {}
        with self._lock:
            self._tick[inst] = {"mark": mk, "bid": _f(q.get("best_bid")), "ask": _f(q.get("best_ask")),
                                "last": _f(data.get("close")), "oi": _f(data.get("oi")),
                                "at": now or time.time()}
        self.last_tick_at = now or time.time()
        return True

    # ------------------------------------------------------------------
    def index_price(self, index_symbol):
        with self._lock:
            return self._index.get(index_symbol)

    def mark_usd(self, instrument, index_symbol=None):
        """An option's mark in dollars - Delta quotes in dollars already, so
        the index is not needed; the argument stays for the shared surface."""
        with self._lock:
            b = self._tick.get(instrument)
        return b["mark"] if b else None

    def book(self, instrument, max_age=None):
        with self._lock:
            b = self._tick.get(instrument)
        if not b:
            return None
        if max_age is not None and time.time() - b["at"] > max_age:
            return None
        return dict(b)

    def forming_bar(self, index_symbol):
        with self._lock:
            bar = self._bars.get(index_symbol)
            return dict(bar) if bar else None

    def age_seconds(self):
        return None if self.last_tick_at is None else time.time() - self.last_tick_at

    def stop(self):
        self._stop.set()
        self.connected = False
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
