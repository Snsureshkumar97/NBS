"""
kite_flow.py — futures open interest, build-up and order flow off the Zerodha feed
================================================================================
Asked for by the user on 18 Sep 2026 ("what more data should we import from kite
for the bot" -> "all"). Everything here is read off the tick socket the index
already streams on, plus one small history call per contract a day. Nothing is
traded, and none of it changes the rule signal: it is context for the bot.

WHAT IT ADDS, per Indian index
    index_future   the near-month future: price, basis to the index, open
                   interest and how it changed since yesterday's close and
                   over the last 15 minutes, the build-up those changes say,
                   and the future's own order flow (VWAP, volume, buy vs sell
                   quantity, the five-level book).
    heavyweights   the same build-up for the index's biggest members' stock
                   futures - a move carried by HDFC Bank and ICICI Bank reads
                   differently from one they sit out.
And order_flow() turns any FULL-mode contract's book into the same traded-side
reading - the feed uses it for the suggested contract and the open tickets'.

BUILD-UP, the standard reading of price against open interest
    price up,   OI up      long build-up     fresh buying
    price down, OI up      short build-up    fresh selling
    price up,   OI down    short covering    sellers leaving
    price down, OI down    long unwinding    buyers leaving
A change smaller than the thresholds below is called "no clear build-up"
rather than forced into one of the four.
"""
import threading
import time

import config
import market_map
from main import now_ist

HEAVY_PER_INDEX = 5          # the heaviest members of each index, by weight
FUT_EXCH = {"NSE": "NFO", "BSE": "BFO"}
STOCK_FUT_EXCH = "NFO"       # stock futures trade on NSE, Sensex members too

DAY_PRICE_EPS = 0.10         # % - smaller day moves are no move for build-up
DAY_OI_EPS = 1.0             # %
WIN_PRICE_EPS = 0.03         # % over the window
WIN_OI_EPS = 0.2             # %
WINDOW_S = 15 * 60           # "the last 15 minutes"
SAMPLE_S = 30                # one sample per contract this often
KEEP_S = 45 * 60
MAX_AGE_S = 120              # a book older than this is not read as live
RETRY_S = 300                # a failed lookup waits this long before trying again
HIST_GAP_S = 0.4             # Zerodha allows three history calls a second

LEAN = {"long build-up": "bullish", "short covering": "bullish, weaker",
        "short build-up": "bearish", "long unwinding": "bearish, weaker"}


def buildup(price_pct, oi_pct, price_eps, oi_eps):
    """One of the four build-ups, "no clear build-up", or None when a number
    is missing."""
    if price_pct is None or oi_pct is None:
        return None
    if abs(price_pct) < price_eps or abs(oi_pct) < oi_eps:
        return "no clear build-up"
    if price_pct > 0:
        return "long build-up" if oi_pct > 0 else "short covering"
    return "short build-up" if oi_pct > 0 else "long unwinding"


def _pct(now, then):
    if now is None or not then:
        return None
    return round((float(now) - float(then)) / float(then) * 100.0, 3)


def order_flow(b):
    """The traded side of one contract, from its FULL-mode book entry: where the
    price sits against the day's VWAP, volume, the quantity waiting to buy
    against to sell, and the lean of the top five levels (+1 all bids, -1 all
    offers). None when there is no book."""
    if not b:
        return None
    ltp, vwap = b.get("ltp"), b.get("vwap")
    buy, sell = b.get("buy_qty"), b.get("sell_qty")
    dbid, dask = b.get("depth_bid_qty"), b.get("depth_ask_qty")
    out = {"ltp": ltp, "vwap": vwap, "vs_vwap_pct": _pct(ltp, vwap) if vwap else None,
           "volume": b.get("volume"), "total_buy_qty": buy, "total_sell_qty": sell,
           "buy_to_sell": round(buy / sell, 2) if buy and sell else None,
           "book_bid_qty_5_levels": dbid, "book_ask_qty_5_levels": dask,
           "book_imbalance": (round((dbid - dask) / (dbid + dask), 2)
                              if dbid is not None and dask is not None and dbid + dask > 0 else None),
           "bid": b.get("bid"), "ask": b.get("ask")}
    return {k: v for k, v in out.items() if v is not None}


def heavyweights(index, n=HEAVY_PER_INDEX):
    """[(symbol, index weight %)] of the index's n heaviest members."""
    rows = sorted(market_map.CONSTITUENTS.get(index) or [], key=lambda r: -r[2])
    return [(sym, w) for sym, _sector, w in rows[:n]]


class Flow:
    """The index futures and the heavyweights' futures of one feed: found once a
    day, streamed in FULL mode, sampled for the 15-minute window."""

    def __init__(self, indices, make_provider, clock=None, today=None, background=True):
        self.indices = list(indices)
        self.make_provider = make_provider      # () -> a KiteDataProvider, or None
        self.clock = clock or time.time
        self.today = today or (lambda: now_ist().strftime("%Y-%m-%d"))   # IST, not this Mac's zone
        self.background = background
        self.lock = threading.Lock()
        self.contracts = {}      # token -> {symbol, name, expiry}
        self.by_index = {}       # index -> {"future": token, "heavy": [(symbol, weight, token)]}
        self.prev = {}           # token -> {"close", "oi"} at yesterday's close
        self.samples = {}        # token -> [(ts, price, oi)], oldest first
        self.day = None
        self.on = None           # the streamer the tokens were last put on
        self.error = None
        self._tried = 0.0
        self._busy = False
        self._sampled = 0.0

    # ------------------------------------------------------------ finding the contracts
    def ensure(self, streamer):
        """From the tick loop: find the contracts (in the background, once a day)
        and put them on this streamer. Costs nothing once both are done."""
        day = self.today()
        with self.lock:
            fresh = self.day == day and self.by_index
            busy = self._busy
        if not fresh and not busy and self.clock() - self._tried >= RETRY_S:
            self._tried = self.clock()
            with self.lock:
                self._busy = True
            if self.background:
                threading.Thread(target=self._resolve, args=(day,), daemon=True,
                                 name="kite-flow").start()
            else:
                self._resolve(day)
        with self.lock:
            tokens = list(self.contracts) if self.day == day else []
            on = self.on
        if tokens and streamer is not None and on is not streamer:
            try:
                streamer.subscribe(tokens, full=True)
                with self.lock:
                    self.on = streamer
            except Exception as exc:
                self.error = f"subscribe failed: {exc}"

    def _resolve(self, day):
        try:
            provider = self.make_provider()
            if provider is None:
                self.error = "no Zerodha session"
                return
            contracts, by_index, prev = {}, {}, {}

            def add(exch, name):
                row = provider.near_future(exch, name)
                if not row:
                    return None
                tok = int(row["instrument_token"])
                contracts[tok] = {"symbol": row.get("tradingsymbol"), "name": name,
                                  "expiry": str(row.get("expiry") or "")[:10]}
                return tok

            for idx in self.indices:
                meta = config.INSTRUMENTS.get(idx) or {}
                exch = FUT_EXCH.get(meta.get("kite_exchange") or "")
                if not exch:
                    continue
                fut = add(exch, meta.get("nse_symbol") or idx)
                heavy = []
                for sym, w in heavyweights(idx):
                    tok = add(STOCK_FUT_EXCH, sym)
                    if tok:
                        heavy.append((sym, w, tok))
                by_index[idx] = {"future": fut, "heavy": heavy}
            for tok in contracts:
                try:
                    prev[tok] = self._previous_close(provider.futures_oi_history(tok), day)
                except Exception:
                    prev[tok] = {}
                if self.background:
                    time.sleep(HIST_GAP_S)
            with self.lock:
                self.contracts, self.by_index, self.prev = contracts, by_index, prev
                self.samples = {t: s for t, s in self.samples.items() if t in contracts}
                self.day, self.on = day, None
            self.error = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                self._busy = False

    @staticmethod
    def _previous_close(bars, day):
        """{close, oi} of the last bar before today - yesterday's closing figures."""
        before = [b for b in bars or [] if str(b.get("date"))[:10] < day]
        if not before:
            return {}
        last = before[-1]
        return {"close": last.get("close"), "oi": last.get("oi")}

    # ------------------------------------------------------------ sampling
    def sample(self, streamer, market_open=True):
        """One sample per contract every SAMPLE_S, while the market is open.
        market_open may be a function, asked only when a sample is due."""
        now = self.clock()
        if streamer is None or now - self._sampled < SAMPLE_S:
            return
        if not (market_open() if callable(market_open) else market_open):
            return
        self._sampled = now
        with self.lock:
            tokens = list(self.contracts)
        for tok in tokens:
            b = streamer.book(tok, max_age=MAX_AGE_S)
            if not b or b.get("ltp") is None or b.get("oi") is None:
                continue
            with self.lock:
                s = self.samples.setdefault(tok, [])
                s.append((now, b["ltp"], b["oi"]))
                while s and now - s[0][0] > KEEP_S:
                    s.pop(0)

    def _window(self, tok, ltp, oi):
        """(price %, OI %, minutes) against the sample from about WINDOW_S ago,
        or None until the feed has run that long."""
        now = self.clock()
        with self.lock:
            old = [x for x in self.samples.get(tok) or [] if now - x[0] >= WINDOW_S - SAMPLE_S]
        if not old or ltp is None or oi is None:
            return None
        ts, px, o = old[-1]
        return _pct(ltp, px), _pct(oi, o), round((now - ts) / 60.0)

    # ------------------------------------------------------------ reading
    def _contract(self, tok, streamer, full=True):
        with self.lock:
            c = dict(self.contracts.get(tok) or {})
            p = dict(self.prev.get(tok) or {})
        b = streamer.book(tok, max_age=MAX_AGE_S) if streamer is not None and tok else None
        if not b:
            return {"contract": c.get("symbol"), "live": False}
        ltp, oi = b.get("ltp"), b.get("oi")
        day_px = _pct(ltp, b.get("prev_close") or p.get("close"))
        day_oi = _pct(oi, p.get("oi"))
        out = {"contract": c.get("symbol"), "live": True, "price": ltp,
               "day_change_pct": day_px, "oi": oi, "oi_change_pct_since_yesterday": day_oi,
               "buildup_today": buildup(day_px, day_oi, DAY_PRICE_EPS, DAY_OI_EPS)}
        win = self._window(tok, ltp, oi)
        if win:
            label = buildup(win[0], win[1], WIN_PRICE_EPS, WIN_OI_EPS)
            out["last_15m"] = {"price_change_pct": win[0], "oi_change_pct": win[1],
                               "minutes": win[2], "buildup": label}
        if full:
            out["expiry"] = c.get("expiry")
            out["oi_day_high"], out["oi_day_low"] = b.get("oi_high"), b.get("oi_low")
            out["order_flow"] = order_flow(b)
        return {k: v for k, v in out.items() if v is not None}

    def reading(self, index, streamer, spot=None):
        """The index future and the heavyweights for one index, or None."""
        with self.lock:
            slot = self.by_index.get(index)
            day_ok = self.day == self.today()
        if not slot or not day_ok:
            return None
        out = {}
        if slot.get("future"):
            fut = self._contract(slot["future"], streamer)
            if spot and fut.get("price") is not None:
                fut["basis_points"] = round(fut["price"] - spot, 2)
                fut["basis_pct"] = round((fut["price"] - spot) / spot * 100.0, 3)
            out["index_future"] = fut
        heavy, up, down = [], 0.0, 0.0
        for sym, w, tok in slot.get("heavy") or []:
            r = self._contract(tok, streamer, full=False)
            r.pop("contract", None)
            r = {"symbol": sym, "index_weight_pct": w, **r}
            lean = LEAN.get(r.get("buildup_today") or "")
            if lean:
                r["lean"] = lean
                if lean.startswith("bullish"):
                    up += w
                else:
                    down += w
            heavy.append(r)
        if heavy:
            out["heavyweights"] = heavy
            out["heavyweights_weight_pct"] = {"bullish_buildup": round(up, 1), "bearish_buildup": round(down, 1),
                                              "of_total": round(sum(h[1] for h in slot["heavy"]), 1)}
        return out or None
