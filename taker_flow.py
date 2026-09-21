"""
taker_flow.py — who is hitting the book: taker buying against taker selling
================================================================================
Asked for by the user on 21 Sep 2026, after reading jarrodwatts/jev-trader,
whose model prompt calls this the strongest short-horizon signal it has.

WHAT IT IS
    Every trade on an exchange has an aggressor: the side that crossed the
    spread and took resting liquidity. Delta labels each print of a contract
    "taker" on the buying or the selling side. Taker buy volume minus taker
    sell volume over a window is the cumulative volume delta (CVD): positive
    when buyers are the ones in a hurry, negative when sellers are.

    It is read on the perpetual (BTCUSD), the deepest book Delta lists, because
    the options that a ticket buys are thin and their prints say little about
    the underlying. Sizes arrive in contracts of 0.001 BTC.

WHAT IT IS NOT
    Not a signal and not backtested: Delta serves only the last few dozen
    prints over REST, so there is no history to test it on. The numbers are
    reference context for the AI desk and the Gann tab, and every reading says
    so. The desk's decisions are stamped with the reading at the time (see
    ai_desk), so a real study can be run once enough of them exist.

HONESTY ABOUT COVERAGE
    The tape lives in memory and starts when the socket connects. A 60-minute
    figure fifteen minutes after a restart is a 15-minute figure; each window
    says whether the tape really covers it (`complete`) and the reading says
    how many minutes the tape holds. If the socket drops and misses prints the
    tape starts again rather than joining two pieces into one wrong total.
"""
import collections
import datetime as dt
import threading
import time

WINDOWS_MIN = (1, 5, 15, 60)
KEEP_S = 2 * 3600
MAX_PRINTS = 90000
GRACE_S = 8                       # a window counts as covered if the tape starts this close to its edge
LIVE_S = 90                       # no print for this long and the reading says it is not live
BUCKET_MIN = 5
BUCKETS = 12                      # the last hour in five-minute steps
LARGE_MIN_CONTRACTS = 500         # 0.5 BTC and up is a large print
LARGE_KEEP = 3
DIVERGE_MIN_MOVE_PCT = 0.15       # price must have moved this much over the 15 minutes ...
DIVERGE_MIN_CVD_PCT = 10.0        # ... and takers lean this hard the other way, to be worth a sentence
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOTE = ("Taker flow on the perpetual: contracts where the buyer crossed the spread against contracts where the "
        "seller did. Not backtested - Delta keeps no history of prints to test it on - so it is context, "
        "not a signal. A window that is not `complete` covers less time than its name says. A candle stand-in "
        "for it (where each bar closed in its range, weighted by volume) did not improve the tool's rules on three "
        "years of Bitcoin history (flow_study.py, 21 Sep 2026); the real flow itself cannot be tested.")


def _num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def side_of(p):
    """+1 when the buyer was the taker, -1 when the seller was, else None."""
    b, s = str(p.get("buyer_role") or "").lower(), str(p.get("seller_role") or "").lower()
    if b == "taker" and s != "taker":
        return 1
    if s == "taker" and b != "taker":
        return -1
    return None


class Tape:
    """The recent prints of one contract, oldest first. Fed straight off the
    socket: `add` takes both message shapes Delta sends - one print at a time
    and the snapshot of the latest few dozen sent right after a subscribe."""

    def __init__(self, keep_s=KEEP_S):
        self.keep_s = keep_s
        self._lock = threading.Lock()
        self._p = collections.deque()          # (t seconds, price, contracts, side)
        self.covered_from = None               # the tape is complete from here on
        self.last_t = None                     # timestamp of the newest print held
        self.last_at = None                    # wall clock when a print last arrived
        self.dropped = 0                       # prints refused as malformed
        self.gaps = 0                          # times a reconnect had missed prints

    def _row(self, p):
        t, px, sz, sd = _num(p.get("timestamp")), _num(p.get("price")), _num(p.get("size")), side_of(p)
        if t is None or px is None or sz is None or sz <= 0 or sd is None:
            return None
        return (t / 1e6, px, sz, sd)             # Delta's timestamps are microseconds

    def add(self, prints, snapshot=False, now=None):
        """Take prints. A snapshot that starts after the newest print already
        held means prints were missed while the socket was down: the tape starts
        again from it. One that overlaps only contributes what is new."""
        now = now or time.time()
        rows = []
        for p in prints or ():
            r = self._row(p)
            if r is None:
                self.dropped += 1
            else:
                rows.append(r)
        if not rows:
            return 0
        rows.sort(key=lambda r: r[0])
        with self._lock:
            if snapshot:
                if self.last_t is not None and rows[0][0] > self.last_t:
                    self._p.clear()
                    self.covered_from = None
                    self.gaps += 1
                if self.last_t is not None:
                    rows = [r for r in rows if r[0] > self.last_t]
            elif self.last_t is not None:
                rows = [r for r in rows if r[0] >= self.last_t]
            if not rows:
                return 0
            if self.covered_from is None:
                self.covered_from = rows[0][0]
            self._p.extend(rows)
            self.last_t = max(self.last_t or 0, rows[-1][0])
            self.last_at = now
            cutoff = self.last_t - self.keep_s
            while self._p and (self._p[0][0] < cutoff or len(self._p) > MAX_PRINTS):
                self._p.popleft()
            if self.covered_from is not None and self._p and self.covered_from < self._p[0][0]:
                self.covered_from = self._p[0][0]
            return len(rows)

    def rows(self):
        with self._lock:
            return list(self._p), self.covered_from, self.last_at


def _window(rows, start, now, covered_from, minutes, unit_per_contract):
    sel = [r for r in rows if r[0] >= start]
    buy = sum(r[2] for r in sel if r[3] > 0) * unit_per_contract
    sell = sum(r[2] for r in sel if r[3] < 0) * unit_per_contract
    vol = buy + sell
    out = {"complete": covered_from is not None and covered_from <= start + GRACE_S,
           "prints": len(sel),
           "taker_buy": round(buy, 3), "taker_sell": round(sell, 3), "cvd": round(buy - sell, 3),
           "cvd_pct_of_volume": round((buy - sell) / vol * 100.0, 1) if vol else None}
    if sel:
        notional = sum(r[1] * r[2] for r in sel)
        contracts = sum(r[2] for r in sel)
        out["vwap"] = round(notional / contracts, 2) if contracts else None
        out["first_price"], out["last_price"] = sel[0][1], sel[-1][1]
        out["price_change_pct"] = round((sel[-1][1] - sel[0][1]) / sel[0][1] * 100.0, 3) if sel[0][1] else None
    return out


def _buckets(rows, now, unit_per_contract):
    step = BUCKET_MIN * 60
    end = int(now // step) * step + step             # the bucket now sits in ends here
    out = []
    for k in range(BUCKETS - 1, -1, -1):
        a, b = end - (k + 1) * step, end - k * step
        sel = [r for r in rows if a <= r[0] < b]
        buy = sum(r[2] for r in sel if r[3] > 0) * unit_per_contract
        sell = sum(r[2] for r in sel if r[3] < 0) * unit_per_contract
        out.append({"until": dt.datetime.fromtimestamp(min(b, now), IST).strftime("%H:%M"),
                    "cvd": round(buy - sell, 3), "volume": round(buy + sell, 3),
                    "close": sel[-1][1] if sel else None, "forming": b > now})
    return out


def _large(rows, start, unit_per_contract):
    big = [r for r in rows if r[0] >= start and r[2] >= LARGE_MIN_CONTRACTS]
    big.sort(key=lambda r: -r[2])
    return [{"at": dt.datetime.fromtimestamp(r[0], IST).strftime("%H:%M:%S"),
             "side": "buy" if r[3] > 0 else "sell", "size": round(r[2] * unit_per_contract, 3), "price": r[1]}
            for r in big[:LARGE_KEEP]]


def _say(w15, unit):
    """One plain sentence about the 15 minutes, or None when it cannot be said
    fairly. It states what happened; it does not say what will."""
    if not w15 or not w15.get("complete") or w15.get("cvd_pct_of_volume") is None or w15.get("price_change_pct") is None:
        return None
    c, p = w15["cvd_pct_of_volume"], w15["price_change_pct"]
    lean = "net buyers" if c > 0 else "net sellers"
    flat = abs(p) < DIVERGE_MIN_MOVE_PCT
    mv = f"was flat ({p:+.2f}%)" if flat else f"{'rose' if p > 0 else 'fell'} {abs(p):.2f}%"
    if abs(c) < DIVERGE_MIN_CVD_PCT:
        return f"Over 15 minutes price {mv}; taker flow was balanced ({c:+.0f}% of volume)."
    if flat:                      # a lean with no move is reported as it is, not called agreement or disagreement
        return f"Over 15 minutes price {mv} while takers were {lean} ({c:+.0f}% of volume)."
    if (p > 0) != (c > 0):
        return (f"Over 15 minutes price {mv} while takers were {lean} ({c:+.0f}% of volume): "
                f"the move and the flow disagree.")
    return f"Over 15 minutes price {mv} with takers {lean} ({c:+.0f}% of volume): the flow agrees."


def reading(tape, unit="BTC", unit_per_contract=0.001, symbol="BTCUSD", now=None):
    """Everything the page and the bot are shown for one contract, or None when
    no print has arrived yet."""
    now = now or time.time()
    rows, covered_from, last_at = tape.rows() if tape is not None else ([], None, None)
    if not rows:
        return None
    age = now - (last_at or now)
    out = {"symbol": f"{symbol} perpetual", "unit": unit,
           "live": age <= LIVE_S, "seconds_since_last_print": round(age),
           "tape_covers_minutes": round(max(0.0, (now - covered_from) / 60.0), 1) if covered_from else 0.0,
           "windows": {}}
    for m in WINDOWS_MIN:
        out["windows"][f"{m}m"] = _window(rows, now - m * 60, now, covered_from, m, unit_per_contract)
    out["five_minute_steps"] = _buckets(rows, now, unit_per_contract)
    out["large_prints_15m"] = _large(rows, now - 900, unit_per_contract)
    out["in_words"] = _say(out["windows"]["15m"], unit)
    out["note"] = NOTE
    return out


def brief(r):
    """The few numbers worth stamping on a decision, so a study of this can be
    run later; None when there is no reading."""
    if not r:
        return None
    w = r.get("windows") or {}
    g = lambda k, f: (w.get(k) or {}).get(f)
    return {"cvd_5m_pct": g("5m", "cvd_pct_of_volume"), "cvd_15m_pct": g("15m", "cvd_pct_of_volume"),
            "price_15m_pct": g("15m", "price_change_pct"), "complete_15m": bool(g("15m", "complete")),
            "live": r.get("live"), "covers_min": r.get("tape_covers_minutes")}
