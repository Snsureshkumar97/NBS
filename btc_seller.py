"""
btc_seller.py — the BTC option SELLER, in PAPER mode only
================================================================================
The user, 2 Oct 2026: "yes build it in paper mode" - after crypto_vol_selling_
study.py found the first BTC strategy profitable after costs in both periods:
SELL the next-day at-the-money straddle at Delta's 17:30 IST settlement, only on
days when options are expensive against BTC's last 7 days, and hold to
settlement (+102k in-sample / +23k held-out per BTC at real spreads; held-out
not conclusive on its own - this paper run is how it earns trust, or doesn't).

PAPER ONLY. This module cannot place an order: it imports no order code and
never reads anyone's Delta keys (btc_seller_test.py checks both). It is one
server-wide book - the market is the same for everyone - read by every viewer.

THE RULE, EXACTLY AS BACKTESTED
  * Once per settlement day, in the hour after 17:30 IST (Delta's daily options
    settle at 12:00 UTC) - an hour, not a minute, so a restart at 17:29 does not
    lose the day.
  * The Delta expiry that settles about a day later (20-30 hours left), at the
    listed strike nearest the index where BOTH the call and the put are quoted.
  * Sold only if the straddle's implied vol (the mean of the two legs' mark IV -
    Delta's own) is ABOVE BTC's realised vol over the last 7 days (15-minute log
    returns, annualised x sqrt(96 x 365), the backtest's own measure).
  * Paper fill at the two BIDS - what selling at market would really get, so the
    spread is paid, not assumed away. Delta's option fee - 0.01% of notional,
    capped at 3.5% of the premium - plus 18% GST, on each leg.
  * Held to settlement, then settled at intrinsic against Delta's index at the
    first check at or after 12:00 UTC; the fee again on a leg that settles in the
    money (as backtested). A settlement checked more than 10 minutes late is
    flagged - its price is then approximate.
  * EVERY day's decision is logged, sold or skipped, with the IV and the realised
    vol - so the live share of "expensive" days can be checked against the
    backtest's (65% in-sample, 48% held-out).
"""
import csv
import datetime as dt
import json
import math
import os
import threading
import time

import config

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEY = "BTC"
SETTLE_UTC_HOUR = 12
WINDOW_MIN = 60
FEE_RATE, FEE_CAP, GST = 0.0001, 0.035, 0.18
CONTRACT_BTC = 0.001                 # one Delta contract
LATE_MIN = 10

DECISION_FIELDS = ["date_ist", "time_ist", "expiry", "strike", "spot", "iv_call", "iv_put", "iv", "rv7",
                   "sell", "reason", "bid_call", "bid_put", "credit"]
TRADE_FIELDS = ["opened_ist", "expiry", "strike", "spot_in", "iv", "rv7", "bid_call", "bid_put", "credit",
                "fees_in", "settled_ist", "settle_spot", "intrinsic_call", "intrinsic_put", "fees_out",
                "pnl_per_btc", "lots", "pnl_usd", "late_min"]


def option_fee(premium, spot):
    """Delta's option fee on one leg, with GST: 0.01% of notional, capped at
    3.5% of the premium."""
    return min(FEE_RATE * spot, FEE_CAP * premium) * (1 + GST)


def realised_vol_7d(closes):
    """Annualised volatility of the last 7 days of 15-minute closes, the
    backtest's measure. None without at least 6 days of bars."""
    xs = [float(c) for c in closes if c is not None and c == c and c > 0]
    rets = [math.log(b / a) for a, b in zip(xs[:-1], xs[1:])][-96 * 7:]
    if len(rets) < 96 * 6:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(96 * 365)


def settle_time(expiry_iso):
    d = dt.date.fromisoformat(str(expiry_iso)[:10])
    return dt.datetime(d.year, d.month, d.day, SETTLE_UTC_HOUR, tzinfo=dt.timezone.utc)


def pick_contract(rows, spot, now):
    """(expiry, call_row, put_row) for the daily that settles 20-30 hours after
    `now`, at the listed strike nearest `spot` with both legs quoted - or None."""
    exps = sorted({r["expiry"] for r in rows
                   if 20 <= (settle_time(r["expiry"]) - now).total_seconds() / 3600 <= 30})
    if not exps:
        return None
    exp = exps[0]
    legs = {}
    for r in rows:
        if r["expiry"] == exp and r.get("bid") and r.get("ask") and r.get("iv"):
            legs.setdefault(r["strike"], {})[r["kind"]] = r
    both = [k for k, v in legs.items() if "C" in v and "P" in v]
    if not both:
        return None
    k = min(both, key=lambda s: (abs(s - spot), s))
    return exp, legs[k]["C"], legs[k]["P"]


def decide(rows, spot, closes, now):
    """Today's decision - a plain dict, logged whether or not anything is sold."""
    out = {"date_ist": now.astimezone(IST).strftime("%Y-%m-%d"),
           "time_ist": now.astimezone(IST).strftime("%H:%M:%S"),
           "expiry": None, "strike": None, "spot": round(float(spot), 2) if spot else None,
           "iv_call": None, "iv_put": None, "iv": None, "rv7": None, "sell": False, "reason": "",
           "bid_call": None, "bid_put": None, "credit": None}
    picked = pick_contract(rows, spot, now) if spot else None
    if picked is None:
        out["reason"] = "no next-day expiry with both legs quoted"
        return out
    exp, c, p = picked
    rv = realised_vol_7d(closes)
    iv = (c["iv"] + p["iv"]) / 2 / 100.0
    out.update({"expiry": exp, "strike": c["strike"], "iv_call": c["iv"], "iv_put": p["iv"],
                "iv": round(iv, 4), "rv7": round(rv, 4) if rv is not None else None,
                "bid_call": c["bid"], "bid_put": p["bid"], "credit": round(c["bid"] + p["bid"], 2)})
    if rv is None:
        out["reason"] = "not enough candles for the 7-day realised vol"
    elif iv > rv:
        out["sell"] = True
        out["reason"] = f"options expensive: implied {iv:.1%} > realised {rv:.1%}"
    else:
        out["reason"] = f"options not expensive: implied {iv:.1%} <= realised {rv:.1%}"
    return out


def settle_pnl(pos, settle_spot):
    """Intrinsic of each leg at settlement, the fee on any leg that settles in
    the money, and the net per 1 BTC and in dollars at the paper size."""
    K = pos["strike"]
    ic, ip = max(0.0, settle_spot - K), max(0.0, K - settle_spot)
    fees_out = (option_fee(ic, settle_spot) if ic > 0 else 0.0) + (option_fee(ip, settle_spot) if ip > 0 else 0.0)
    per_btc = pos["credit"] - pos["fees_in"] - ic - ip - fees_out
    return {"intrinsic_call": round(ic, 2), "intrinsic_put": round(ip, 2), "fees_out": round(fees_out, 2),
            "pnl_per_btc": round(per_btc, 2), "pnl_usd": round(per_btc * pos["lots"] * CONTRACT_BTC, 2)}


class SellerBook:
    """The paper book: at most one open straddle, a decision per settlement day."""

    def __init__(self, folder, provider, clock=None, lots=None):
        self.folder = folder
        self.provider = provider
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.lots = lots
        self.lock = threading.Lock()
        self.mark = None                       # live buy-back value of the open straddle, display only
        self.error = None
        os.makedirs(folder, exist_ok=True)
        self.state = self._load()

    # ------------------------------------------------------------ files
    def _path(self, name):
        return os.path.join(self.folder, name)

    def _load(self):
        try:
            with open(self._path("state.json")) as fh:
                return json.load(fh)
        except Exception:
            return {"open": None, "last_key": None, "last_decision": None}

    def _save(self):
        tmp = self._path("state.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(self.state, fh, indent=1, default=str)
        os.replace(tmp, self._path("state.json"))

    def _append(self, name, fields, row):
        path = self._path(name)
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def trades(self):
        try:
            with open(self._path("trades.csv")) as fh:
                return list(csv.DictReader(fh))
        except FileNotFoundError:
            return []

    # ------------------------------------------------------------ the cycle
    def tick(self, now=None):
        """Settle what has expired, then - inside the window, once per
        settlement day - decide and maybe sell. Returns what it did."""
        if not getattr(config, "BTC_SELLER_PAPER", False):
            return "off"
        now = now or self.clock()
        did = []
        with self.lock:
            pos = self.state.get("open")
            if pos and now >= dt.datetime.fromisoformat(pos["settle_at"]):
                spot = float(self.provider.spot(KEY))
                res = settle_pnl(pos, spot)
                late = (now - dt.datetime.fromisoformat(pos["settle_at"])).total_seconds() / 60
                row = dict(pos, settled_ist=now.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                           settle_spot=round(spot, 2), late_min=round(late, 1), **res)
                self._append("trades.csv", TRADE_FIELDS, row)
                self.state["open"] = None
                self.mark = None
                self._save()
                did.append("settled")

            ist = now.astimezone(IST)
            start = ist.replace(hour=17, minute=30, second=0, microsecond=0)
            if start <= ist < start + dt.timedelta(minutes=WINDOW_MIN):
                key = (ist.date() + dt.timedelta(days=1)).isoformat()     # the expiry it would sell
                if self.state.get("last_key") != key:
                    rows = self.provider._chain_rows(KEY)
                    spot = float(self.provider.spot(KEY))
                    candles = self.provider.get_ohlc(KEY, "15m", lookback_days=8)
                    d = decide(rows, spot, list(candles["Close"]), now)
                    if d["expiry"] is None or d["rv7"] is None:
                        # Not a decision yet - e.g. the new day's contracts are still being
                        # listed right at 17:30. Shown, not logged, and tried again next minute
                        # for the rest of the hour rather than writing the day off.
                        self.state["last_decision"] = d
                        return "waiting"
                    self._append("decisions.csv", DECISION_FIELDS, d)
                    self.state["last_key"] = key
                    self.state["last_decision"] = d
                    if d["sell"] and self.state.get("open") is None:
                        lots = int(self.lots if self.lots is not None else getattr(config, "BTC_SELLER_LOTS", 250))
                        fees_in = option_fee(d["bid_call"], spot) + option_fee(d["bid_put"], spot)
                        self.state["open"] = {
                            "opened_ist": ist.strftime("%Y-%m-%d %H:%M:%S"), "expiry": d["expiry"],
                            "settle_at": settle_time(d["expiry"]).isoformat(), "strike": d["strike"],
                            "spot_in": round(spot, 2), "iv": d["iv"], "rv7": d["rv7"],
                            "bid_call": d["bid_call"], "bid_put": d["bid_put"], "credit": d["credit"],
                            "fees_in": round(fees_in, 2), "lots": lots}
                        did.append("sold")
                    else:
                        did.append("skipped")
                    self._save()

            if self.state.get("open"):
                self._remark()
        return ",".join(did) or "idle"

    def _remark(self):
        """What buying the open straddle back would cost right now, at the asks -
        display only, never a decision."""
        pos = self.state["open"]
        try:
            rows = self.provider._chain_rows(KEY)
            ask = {r["kind"]: r.get("ask") for r in rows
                   if r["expiry"] == pos["expiry"] and r["strike"] == pos["strike"]}
            if ask.get("C") and ask.get("P"):
                self.mark = round(ask["C"] + ask["P"], 2)
        except Exception:
            pass

    # ------------------------------------------------------------ the page
    def public(self):
        with self.lock:
            pos = self.state.get("open")
            rows = self.trades()
        pnl = [float(r["pnl_usd"]) for r in rows if r.get("pnl_usd") not in (None, "")]
        open_view = None
        if pos:
            open_view = dict(pos)
            if self.mark is not None:
                per_btc = pos["credit"] - pos["fees_in"] - self.mark
                open_view.update({"buyback_now": self.mark, "unrealised_usd": round(per_btc * pos["lots"] * CONTRACT_BTC, 2)})
        return {"enabled": bool(getattr(config, "BTC_SELLER_PAPER", False)), "paper": True,
                "lots": int(self.lots if self.lots is not None else getattr(config, "BTC_SELLER_LOTS", 250)),
                "last_decision": self.state.get("last_decision"), "open": open_view,
                "record": {"trades": len(pnl), "total_usd": round(sum(pnl), 2), "wins": sum(1 for x in pnl if x > 0),
                           "last": rows[-10:][::-1]},
                "error": self.error}


BOOK = None


def start_background(stop_event=None, provider=None, folder=None):
    """The server-wide paper book, ticking once a minute. Nothing at all when
    the seller or crypto is switched off."""
    global BOOK
    if not getattr(config, "BTC_SELLER_PAPER", False) or not getattr(config, "ENABLE_CRYPTO", True):
        return None
    if provider is None:
        import delta_provider
        provider = delta_provider.DeltaDataProvider()
    if folder is None:
        import trade_log
        folder = os.path.join(trade_log.log_dir(), "btc_seller")
    BOOK = SellerBook(folder, provider)

    def loop():
        while stop_event is None or not stop_event.is_set():
            try:
                BOOK.tick()
                BOOK.error = None
            except Exception as e:                # a bad reply never stops the loop
                BOOK.error = f"{type(e).__name__}: {e}"[:200]
            for _ in range(60):
                if stop_event is not None and stop_event.is_set():
                    return
                time.sleep(1)

    t = threading.Thread(target=loop, daemon=True, name="btc-seller")
    t.start()
    return t
