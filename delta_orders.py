"""
delta_orders.py — real Delta Exchange India orders that follow a Bitcoin ticket
================================================================================
The crypto twin of live_orders.py, asked for by the user on 20 Sep 2026. A
switch for Bitcoin (one for the rule tickets, one for the AI desk's) makes the
tool take each ticket as a real order on Delta Exchange India, with the
ticket's own stop and target, under the account's own Delta keys
(user_delta.py).

WHAT A TICKET BECOMES, WHEN BITCOIN IS SWITCHED ON
    1. BUY the ticket's exact contract (C-BTC-<strike>-<DDMMYY>), `lots`
       contracts of 0.001 BTC, as a limit a little above the mark. Anything
       unfilled after FILL_WAIT_S is cancelled and only what filled is carried.
    2. THE STOP LIVES IN THIS TOOL. Delta does not accept stop orders on
       options, so nothing rests at the venue: every second the mark is read
       and, at or under the ticket's stop, the position is sold. That only
       works while this server is running - if it is off, the stop is not
       watched. The Zerodha executor is different: its stop sits at the
       exchange. Say so to anyone switching this on.
       As the ticket's own staircase trailing stop moves (tickets.py's
       _check_price(), added 22 Sep 2026), the level this tool watches moves
       with it - no order to reprice, since none rests at Delta; the next
       mark read simply compares against the new level.
    3. The ticket closes (target, stop, AI exit, cleared): whatever is held is
       sold with a limit a little under the mark, repriced every REPRICE_S; if
       the limit will not fill after LIMIT_ATTEMPTS tries, a market sell that
       can only reduce the position finishes it - there is no exchange
       square-off to fall back on.
    4. An option settles at 12:00 UTC on its expiry day. A position is sold
       EXPIRY_EXIT_MIN before that, ticket and all, and no entry is placed
       within NO_ENTRY_BEFORE_EXPIRY_MIN of it.

WHAT IS NEVER DONE
    No order without the account's Delta keys, for a ticket priced off the
    index rather than a live premium, from a second ticket while one position
    is held, beyond MAX_ENTRIES_PER_DAY, or when the wallet cannot pay for it.
    Every step is written to <trade log>.delta.json; closed positions write
    their real fills to <trade log>.delta.fills.jsonl.

NEVER LOSING TRACK OF A REAL POSITION
    The first real Delta order (24 Sep 2026, a second account) was bought and then
    written off as "closed outside the tool" 15 seconds later, so when its stop hit
    nothing was sent and the position was left open on Delta. Delta's answer for a
    single product carries no product_id (with nothing held it is just
    {"size": 0, "entry_price": null}), and the old reading dropped any row without
    one, so a held position read as zero. Now: the reply is read as Delta sends it
    (position_size); "cannot tell" is never zero; two readings in a row must show
    fewer contracts than were bought before the tool believes they were sold
    elsewhere; when the ticket then closes the tool looks once more and still sells
    if anything is held; and a sell is tried even when the reading says zero - it is
    reduce-only and can open nothing, and only Delta refusing it (or it filling
    nothing) twice, with none held, takes the position as gone.

RESTARTS
    A restart closes every ticket in the record, so a real position found
    open on start is sold too - the record and the account must not disagree
    about whether you are in a trade.
"""
import json
import math
import os
import queue
import threading
import time

import config
from main import now_ist

# BITCOIN ONLY, on purpose: gold (GOLD, added 21 Sep 2026) is paper-only - it must
# not be added here without converting a ticket's lots (100 contracts each) to
# whole contracts, and without a paper record that justifies its 6-8% spreads.
INDICES = ("BTC",)
ENTRY_BUFFER = 0.02
EXIT_BUFFER = 0.03
FILL_WAIT_S = 20
REPRICE_S = 5
LIMIT_ATTEMPTS = 3           # limit sells before a reduce-only market sell finishes it
MAX_ENTRIES_PER_DAY = 10
VERIFY_S = 15                # how often an open position is checked against Delta's own positions
SETTLE_S = 10                # positions can lag a fill by a moment; not trusted to shrink a holding sooner
FLAT_CONFIRMS = 2            # readings in a row showing fewer contracts than held, before believing they were sold elsewhere
FLAT_REFUSALS = 2            # sells refused or filling nothing, with none held, before taking the position as gone
AUTH_CODES = ("ip_not_whitelisted_for_api_key", "unauthorized", "invalid_api_key", "invalid_signature", "expired_signature")
PLACING_WAIT_S = 15
POLL_S = 1.0
NOTES_KEPT = 30
FUNDS_CACHE_S = 30
FEE_ALLOWANCE = 0.02         # of the premium, kept aside for Delta's fees
EXPIRY_EXIT_MIN = 30
NO_ENTRY_BEFORE_EXPIRY_MIN = 60
SETTLE_HOUR_UTC = 12

ACTIVE = ("placing", "entering", "open", "exiting", "attention")

_executors = {}
_registry_lock = threading.Lock()


def for_account(email, log_path, close_ticket, client=None, mark=None):
    """One executor per account for the whole process."""
    with _registry_lock:
        ex = _executors.get(email)
        if ex is None:
            ex = _executors[email] = Executor(email, log_path, client=client, close_ticket=close_ticket, mark=mark)
        else:
            ex.close_ticket = close_ticket
        return ex


def fills_path(log_path):
    return (log_path + ".delta.fills.jsonl") if log_path else None


def read_fills(log_path, source=None):
    path = fills_path(log_path)
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if source is None or r.get("source", "rule") == source:
                    out.append(r)
    except (OSError, TypeError):
        pass
    return out


def _floor_tick(x, tick):
    return round(math.floor(x / tick) * tick, 6)


def _ceil_tick(x, tick):
    return round(math.ceil(x / tick) * tick, 6)


def _unanswered(exc):
    return type(exc).__name__ in ("Timeout", "ReadTimeout", "ConnectionError", "ConnectTimeout", "NetworkException")


def _infra_error(exc):
    """A refusal that says nothing about whether a position exists: the key, the IP, the
    clock, the network, Delta being down or rate-limiting. It must never be read as "flat"."""
    code = str(getattr(exc, "code", "") or "")
    return _unanswered(exc) or code in AUTH_CODES or code.startswith("http 5") or code.startswith("http 429") or "rate" in code


def position_size(reply, product_id, symbol=None):
    """Contracts Delta says are held on one product, or None when the reply cannot be read.

    Delta's position call is already scoped to a product, and for one with nothing held
    it answers a bare {"size": 0, "entry_price": null} - no product_id in it (seen live,
    24 Sep 2026). A row that names no product is therefore taken to be for this one; a row
    naming ANOTHER product is not. A reply with no usable size (nothing, an empty list,
    {"size": null}, text) is None - not knowing is never the same as holding nothing. A
    negative size is a short, impossible after buying, and is also unknown."""
    if reply is None:
        return None
    total, seen = 0, False
    for r in (reply if isinstance(reply, list) else [reply]):
        if not isinstance(r, dict) or "size" not in r:
            continue
        pid = r.get("product_id")
        try:
            if pid not in (None, "") and int(pid) != int(product_id):
                continue
        except (TypeError, ValueError):
            continue
        sym = r.get("product_symbol")
        if sym and symbol and sym != symbol:
            continue
        try:
            n = int(float(r.get("size")))
        except (TypeError, ValueError):
            return None
        if n < 0:
            return None
        total, seen = total + n, True
    return total if seen else None


# =============================================================================
class DeltaClient:
    """The few signed and public calls the executor makes, over
    user_delta.request. Kept apart so a fake can stand in for all of it."""

    def __init__(self, key, secret, session=None):
        self.key, self.secret, self.session = key, secret, session

    def _call(self, method, path, params=None, body=None):
        import user_delta
        return user_delta.request(self.key, self.secret, method, path, params=params, body=body,
                                  session=self.session)

    def product(self, symbol):
        return self._call("GET", f"/v2/products/{symbol}")

    def ticker(self, symbol):
        return self._call("GET", f"/v2/tickers/{symbol}")

    def balances(self):
        return self._call("GET", "/v2/wallet/balances")

    def place_order(self, **kw):
        return self._call("POST", "/v2/orders", body=kw)

    def cancel_order(self, order_id, product_id):
        return self._call("DELETE", "/v2/orders", body={"id": int(order_id), "product_id": int(product_id)})

    def edit_order(self, order_id, product_id, **kw):
        return self._call("PUT", "/v2/orders", body=dict({"id": int(order_id), "product_id": int(product_id)}, **kw))

    def order(self, order_id):
        return self._call("GET", f"/v2/orders/{int(order_id)}")

    def open_orders(self, product_id):
        return self._call("GET", "/v2/orders", params={"product_ids": str(product_id), "states": "open,pending"})

    def position(self, product_id):
        return self._call("GET", "/v2/positions", params={"product_id": str(product_id)})


def _client_factory(email):
    def make():
        import user_delta
        keys = user_delta.keys_for(email)
        if not keys:
            raise RuntimeError("no Delta Exchange keys on this account")
        return DeltaClient(keys[0], keys[1])
    return make


def _order_state(o):
    """Delta's order object, read the one way: (state, filled, average price)."""
    o = o or {}
    size = int(o.get("size") or 0)
    unfilled = int(o.get("unfilled_size") or 0)
    filled = max(0, size - unfilled)
    try:
        avg = float(o.get("average_fill_price")) if o.get("average_fill_price") not in (None, "") else None
    except (TypeError, ValueError):
        avg = None
    return (o.get("state") or ""), filled, avg


# =============================================================================
class Executor:
    """One account's live Bitcoin orders on Delta Exchange India."""

    def __init__(self, email, log_path, client=None, close_ticket=None, now=None, clock=None, mark=None, start=True):
        self.email = email
        self.path = (log_path + ".delta.json") if log_path else None
        self.log_path = log_path
        self.make_client = client or _client_factory(email)
        self.close_ticket = close_ticket or (lambda index, status: None)
        self.now = now or now_ist
        self.clock = clock or time.time
        self.mark_source = mark             # symbol -> live mark, or None to read Delta's ticker
        self.lock = threading.RLock()
        self.q = queue.Queue()
        self.enabled = {k: False for k in INDICES}
        self.enabled_ai = {k: False for k in INDICES}
        self.positions = {}
        self.notes = []
        self.day = None
        self.entries_today = 0
        self._client = None
        self._funds = None
        self._products = {}
        self._load()
        self._recover()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._loop, daemon=True, name=f"delta:{email}")
            self.thread.start()

    # ------------------------------------------------------------ disk
    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path) as fh:
                s = json.load(fh)
        except Exception:
            return
        self.enabled.update({k: bool(v) for k, v in (s.get("enabled") or {}).items() if k in INDICES})
        self.enabled_ai.update({k: bool(v) for k, v in (s.get("enabled_ai") or {}).items() if k in INDICES})
        self.positions = s.get("positions") or {}
        self.notes = s.get("notes") or []
        self.day = s.get("day")
        self.entries_today = int(s.get("entries_today") or 0)

    def _save(self):
        if not self.path:
            return
        with self.lock:
            today = self._today()
            keep = {t: p for t, p in self.positions.items() if p.get("day") == today or p.get("state") in ACTIVE}
            self.positions = keep
            data = {"enabled": self.enabled, "enabled_ai": self.enabled_ai, "positions": keep,
                    "notes": self.notes[:NOTES_KEPT], "day": self.day, "entries_today": self.entries_today}
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh, default=str)
            os.replace(tmp, self.path)

    def _today(self):
        return self.now().strftime("%Y-%m-%d")

    def _note(self, index, text, level="info", pos=None):
        with self.lock:
            entry = {"at": self.now().strftime("%H:%M:%S"), "index": index, "text": text, "level": level}
            self.notes.insert(0, entry)
            del self.notes[NOTES_KEPT:]
            if pos is not None:
                pos.setdefault("log", []).append(entry)
        print(f"[delta] {self.now():%Y-%m-%d %H:%M:%S} {index}: {text}", flush=True)

    # ------------------------------------------------------------ the switch
    def switches(self, source="rule"):
        return self.enabled_ai if source == "ai" else self.enabled

    def set_enabled(self, index, on, source="rule"):
        if index not in INDICES:
            raise ValueError("Only Bitcoin places orders on Delta Exchange.")
        if source not in ("rule", "ai"):
            raise ValueError("Unknown kind of ticket.")
        with self.lock:
            self.switches(source)[index] = bool(on)
            self._save()
        what = "AI trades on " + index if source == "ai" else index
        self._note(index, f"Live Delta orders for {what} switched "
                          + ("ON. The stop is watched by this tool, not held at Delta." if on
                             else "OFF. An open position is still managed to its exit."))
        return self.switches(source)[index]

    # ------------------------------------------------------------ ticket events
    def on_ticket_event(self, kind, trade, source="rule", **info):
        if not trade or trade.get("index") not in INDICES:
            return
        info = dict(info, source=source)
        if kind == "opened":
            self.q.put(("open", dict(trade), info))
        elif kind == "closed":
            self.q.put(("close", {"trade_id": trade.get("trade_id"), "index": trade.get("index"),
                                  "status": trade.get("status")}, info))
        elif kind == "trailed":
            self.q.put(("trail", dict(trade), info))

    def _loop(self):
        while True:
            try:
                item = self.q.get(timeout=POLL_S)
            except queue.Empty:
                item = None
            try:
                if item:
                    self.handle(*item)
                self.poll()
            except Exception as exc:
                self._note("-", f"Unexpected error in the Delta order loop: {type(exc).__name__}: {exc}", "error")

    def handle(self, what, trade, info):
        if what == "open":
            self._enter(trade, (info or {}).get("source") or "rule")
        elif what == "trail":
            pos = self.positions.get(trade.get("trade_id"))
            if pos is not None:
                self._reprice_stop(pos, trade.get("premium_sl"))
        else:
            pos = self.positions.get(trade.get("trade_id"))
            if pos is not None:
                reason = (f"the ticket closed ({trade.get('status') or 'closed'})" if what == "close"
                          else info.get("reason") or "the tool restarted while this position was open")
                pos["ticket_closed"] = reason
                if pos.get("state") == "closed" and pos.get("outside_unconfirmed"):
                    self._reconsider(pos)
                self._exit(pos, reason)
        self._save()

    def _reconsider(self, pos):
        """A position written off as closed outside the tool is looked at once more the
        moment its ticket closes. Positively none held: it stays closed. Held, or the answer
        cannot be read: it is put back so the normal sell runs - a sell that is reduce-only
        can open nothing, so wrongly trying costs nothing and wrongly not trying costs the
        position (24 Sep 2026)."""
        net = self._net_qty(pos)
        if net == 0:
            pos["outside_unconfirmed"] = False
            self._note(pos["index"], "Delta confirms nothing is held - no sell needed.", pos=pos)
            return
        pos["state"] = "open"
        pos["flat_reads"] = 0
        bought = int(pos.get("filled_qty") or 0) - int(pos.get("exit_filled") or 0)
        pos["outside_sold"] = max(0, bought - net) if net else 0
        self._note(pos["index"], (f"The position had been written off as closed outside the tool, but Delta still "
                                  f"shows {net} held" if net else "The position had been written off as closed outside "
                                  "the tool, but Delta's answer cannot be read now")
                   + " - selling it.", "warn", pos)

    def _gone(self, pos, why):
        """Delta refuses to sell it and shows none held: it is not there."""
        pos["outside_sold"] = int(pos.get("outside_sold") or 0) + max(0, self._held(pos))
        pos["state"], pos["exit_order_id"] = "closed", None
        pos.setdefault("exit_reason", "it was closed outside the tool")
        self._note(pos["index"], f"{why} - taking the position as already closed.", "warn", pos)

    # ------------------------------------------------------------ delta
    def client(self):
        if self._client is None:
            self._client = self.make_client()
        return self._client

    def _product(self, symbol):
        p = self._products.get(symbol)
        if p is None:
            r = self.client().product(symbol) or {}
            p = self._products[symbol] = {
                "id": int(r["id"]), "tick": float(r.get("tick_size") or 0.1),
                "contract_value": float(r.get("contract_value") or 0.001),
                "settling_asset": ((r.get("settling_asset") or {}).get("symbol") or "USD"),
                "settlement_time": r.get("settlement_time"),
            }
        return p

    def _mark(self, pos):
        if self.mark_source is not None:
            try:
                m = self.mark_source(pos["symbol"])
                if m:
                    return float(m)
            except Exception:
                pass
        try:
            t = self.client().ticker(pos["symbol"]) or {}
            m = t.get("mark_price") or t.get("close")
            return float(m) if m not in (None, "") else None
        except Exception:
            return None

    def _order(self, order_id):
        return self.client().order(order_id) or {}

    def _place(self, pos, **kw):
        r = self.client().place_order(product_id=pos["product_id"], time_in_force="gtc", **kw) or {}
        return str(r["id"])

    # ------------------------------------------------------------ funds
    def _available(self, asset, fresh=False):
        now = self.clock()
        if not fresh and self._funds and now - self._funds[0] < FUNDS_CACHE_S and self._funds[1] == asset:
            return self._funds[2]
        rows = self.client().balances() or []
        avail = None
        for b in rows:
            if (b.get("asset_symbol") or "").upper() == asset.upper():
                try:
                    avail = float(b.get("available_balance"))
                except (TypeError, ValueError):
                    avail = None
        self._funds = (now, asset, avail)
        return avail

    def funds_check(self, need, asset="USD"):
        """{"enough": True/False, "shortfall": ...} or {"enough": None} when it
        cannot be read - for the AI desk; never the balance itself."""
        try:
            avail = self._available(asset)
        except Exception:
            avail = None
        if avail is None or not need:
            return {"enough": None, "note": "Delta's wallet could not be read just now."}
        short = round(float(need) * (1 + FEE_ALLOWANCE) - avail, 2)
        return {"enough": short <= 0, "shortfall": short if short > 0 else None}

    # ------------------------------------------------------------ expiry
    def _settle_epoch(self, pos):
        st = (pos.get("settlement_time") or "")
        try:
            import datetime as dt
            d = dt.datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            return d.timestamp()
        except Exception:
            try:
                import datetime as dt
                d = dt.date.fromisoformat(str(pos.get("expiry"))[:10])
                return dt.datetime(d.year, d.month, d.day, SETTLE_HOUR_UTC, tzinfo=dt.timezone.utc).timestamp()
            except Exception:
                return None

    def _minutes_to_settle(self, pos):
        s = self._settle_epoch(pos)
        return None if s is None else (s - self.clock()) / 60.0

    # ------------------------------------------------------------ entry
    def _enter(self, trade, source="rule"):
        index, tid = trade["index"], trade.get("trade_id")
        who = "AI " if source == "ai" else ""
        now = self.now()
        today = self._today()
        with self.lock:
            if self.day != today:
                self.day, self.entries_today = today, 0
            if not self.switches(source).get(index):
                return
            if not tid or tid in self.positions:
                return
            if any(p.get("index") == index and p.get("source", "rule") == source and p.get("state") in ACTIVE
                   for p in self.positions.values()):
                self._note(index, f"No live order: Bitcoin already holds a live {who}position.", "warn")
                return
            if self.entries_today >= MAX_ENTRIES_PER_DAY:
                self._note(index, f"No live order: {MAX_ENTRIES_PER_DAY} live entries today is the hard cap.", "warn")
                return
            if not trade.get("use_premium") or trade.get("premium_sl") is None:
                self._note(index, "No live order: this ticket is priced off the index, not a live premium.", "warn")
                return
            pos = self.positions[tid] = {
                "trade_id": tid, "index": index, "day": today, "state": "placing", "source": source,
                "strike": trade.get("strike"), "option_type": trade.get("option_type"),
                "expiry": str(trade.get("expiry") or "")[:10], "lots": trade.get("lots") or 1,
                "stop_trigger": trade.get("premium_sl"), "target": (trade.get("premium_targets") or [None])[0],
                "paper_entry": trade.get("entry_ltp"),
                "tag": ("TPBTC" + now.strftime("%d%H%M%S")),
                "entry_order_id": None, "exit_order_id": None,
                "filled_qty": 0, "avg_price": None, "exit_filled": 0, "exit_value": 0.0, "exit_priced": 0,
                "exit_attempts": 0, "created": now.isoformat(), "log": [],
            }
            self.entries_today += 1
            self._save()
        try:
            try:
                self.client()
            except Exception as exc:
                return self._fail(pos, f"No live order: {exc}. Add your Delta Exchange keys on the Delta page.")
            import delta_provider
            symbol = delta_provider.DeltaDataProvider().option_instrument(index, pos["strike"], pos["option_type"],
                                                                          pos["expiry"] or None)
            if not symbol:
                return self._fail(pos, f"Delta lists no {index} {pos['strike']} {pos['option_type']} "
                                       f"expiring {pos['expiry']} - no order placed.")
            prod = self._product(symbol)
            qty = int(float(pos["lots"]))
            if qty < 1:
                return self._fail(pos, "No live order: the ticket is for less than one contract.")
            pos.update(symbol=symbol, product_id=prod["id"], tick=prod["tick"], contract_value=prod["contract_value"],
                       settling_asset=prod["settling_asset"], settlement_time=prod["settlement_time"], qty=qty)
            mins = self._minutes_to_settle(pos)
            if mins is not None and mins < NO_ENTRY_BEFORE_EXPIRY_MIN:
                return self._fail(pos, f"No live order: this contract settles in {mins:.0f} minutes.")
            mark = self._mark(pos) or trade.get("entry_ltp")
            if not mark:
                return self._fail(pos, "No live order: no live price for the contract.")
            limit = _ceil_tick(float(mark) * (1 + ENTRY_BUFFER), pos["tick"])
            pos["entry_limit"] = limit
            need = limit * qty * pos["contract_value"]
            try:
                avail = self._available(pos["settling_asset"], fresh=True)
            except Exception as exc:
                avail = None
                self._note(index, f"Could not read the Delta wallet ({exc}) - sending the order anyway; Delta "
                                  "checks funds itself.", "warn", pos)
            if avail is not None and need * (1 + FEE_ALLOWANCE) > avail:
                pos["funds_short"] = round(need * (1 + FEE_ALLOWANCE) - avail, 2)
                return self._fail(pos, f"Not enough funds on Delta: this order needs about "
                                       f"{pos['settling_asset']} {need * (1 + FEE_ALLOWANCE):,.2f} ({qty} x {limit} x "
                                       f"{pos['contract_value']} plus fees) and the wallet is short by "
                                       f"{pos['settling_asset']} {pos['funds_short']:,.2f}. No order sent.")
            pos["sending"] = True
            pos["entry_order_id"] = self._place(pos, size=qty, side="buy", order_type="limit_order",
                                                limit_price=str(limit), client_order_id=pos["tag"])
            pos["state"], pos["entry_at"] = "entering", self.clock()
            self._note(index, f"{who}ticket: BUY {qty} {symbol} limit {limit} sent (order {pos['entry_order_id']}).", pos=pos)
        except Exception as exc:
            if pos.get("sending") and not pos.get("entry_order_id") and _unanswered(exc):
                pos["state"], pos["placing_at"] = "placing", self.clock()
                self._note(index, f"Delta did not answer the entry ({exc}) - checking whether it arrived.", "warn", pos)
            else:
                self._fail(pos, f"Entry order refused: {exc}")
        finally:
            self._save()

    def _fail(self, pos, text):
        pos["state"] = "failed"
        self._note(pos["index"], text + " The ticket carries on as a paper ticket.", "error", pos)
        self._save()

    def _check_entry(self, pos):
        state, filled, avg = _order_state(self._order(pos["entry_order_id"]))
        pos["filled_qty"] = filled
        if avg:
            pos["avg_price"] = avg
        if state == "closed" or (state == "cancelled" and filled > 0):
            return self._hold(pos)
        if state == "cancelled":
            return self._fail(pos, "Entry order cancelled by Delta: nothing bought.")
        if self.clock() - pos.get("entry_at", 0) >= FILL_WAIT_S:
            try:
                self.client().cancel_order(pos["entry_order_id"], pos["product_id"])
            except Exception:
                pass
            state, filled, avg = _order_state(self._order(pos["entry_order_id"]))
            pos["filled_qty"] = filled
            if avg:
                pos["avg_price"] = avg
            if filled > 0:
                self._note(pos["index"], f"Entry only partly filled in {FILL_WAIT_S}s: carrying {filled} of {pos['qty']}.",
                           "warn", pos)
                return self._hold(pos)
            return self._fail(pos, f"Entry not filled within {FILL_WAIT_S}s at {pos['entry_limit']} or better - "
                                   "cancelled, nothing bought.")

    def _hold(self, pos):
        """Bought. The stop is this tool's to watch: Delta holds no stop order
        on an option."""
        self._note(pos["index"], f"Bought {pos['filled_qty']} at {pos['avg_price']}. Stop {pos['stop_trigger']} is "
                                 "watched by this tool on Delta's mark - nothing rests at the venue.", pos=pos)
        pos["filled_at"] = pos["verified_at"] = self.clock()
        pos["state"] = "open"
        if pos.get("ticket_closed"):
            return self._exit(pos, pos["ticket_closed"])
        if pos["avg_price"] is not None and float(pos["avg_price"]) <= float(pos["stop_trigger"]):
            return self._exit(pos, "it filled at or below the stop")

    def _reprice_stop(self, pos, new_sl):
        """Move the level this tool watches up to a rung the ticket just
        trailed to (tickets.py's staircase trailing stop, 22 Sep 2026).

        No broker order to modify - Delta holds none on an option - so this
        is just the in-memory level _check_open() compares the mark against.
        """
        if pos.get("state") != "open" or new_sl is None:
            return
        trig = _floor_tick(float(new_sl), pos.get("tick", 0.1))
        if trig <= pos["stop_trigger"]:
            return                      # not an improvement once rounded to a tick - nothing to do
        pos["stop_trigger"] = trig
        self._note(pos["index"], f"Stop trailed to {trig} - still watched by this tool, not at Delta.", pos=pos)

    # ------------------------------------------------------------ holding
    def _check_open(self, pos):
        mins = self._minutes_to_settle(pos)
        if mins is not None and mins <= EXPIRY_EXIT_MIN:
            try:
                self.close_ticket(pos["index"], f"CLOSED — sold {EXPIRY_EXIT_MIN} min before the contract settles (live order)")
            except Exception:
                pass
            return self._exit(pos, f"the contract settles in {mins:.0f} minutes")
        if self.clock() - pos.get("verified_at", 0) >= VERIFY_S:
            pos["verified_at"] = self.clock()
            net = self._net_qty(pos)
            if net is not None:
                if net >= self._held(pos):
                    pos["flat_reads"] = 0
                else:
                    pos["flat_reads"] = int(pos.get("flat_reads") or 0) + 1
                    if pos["flat_reads"] >= FLAT_CONFIRMS:
                        return self._closed_outside(pos, net)
                    self._note(pos["index"], f"Delta shows {net} held where the tool bought {self._held(pos)} - "
                                             "reading it again before believing that.", "warn", pos)
        mark = self._mark(pos)
        if mark is not None:
            pos["mark"] = mark
            if mark <= float(pos["stop_trigger"]):
                try:
                    self.close_ticket(pos["index"], "CLOSED — stop-loss hit (live order, Delta mark)")
                except Exception:
                    pass
                return self._exit(pos, f"the mark {mark} reached the stop {pos['stop_trigger']}")

    # ------------------------------------------------------------ exit
    def _exit(self, pos, reason):
        if pos.get("state") not in ("entering", "open"):
            return
        if pos.get("state") == "entering" and pos.get("entry_order_id"):
            try:
                self.client().cancel_order(pos["entry_order_id"], pos["product_id"])
            except Exception:
                pass
            _s, filled, avg = _order_state(self._order(pos["entry_order_id"]))
            pos["filled_qty"] = filled
            if avg:
                pos["avg_price"] = avg
        pos["exit_reason"] = reason
        self._sell_rest(pos)

    def _held(self, pos):
        return int(pos.get("filled_qty") or 0) - int(pos.get("exit_filled") or 0) - int(pos.get("outside_sold") or 0)

    def _net_qty(self, pos):
        if self.clock() - pos.get("filled_at", 0) < SETTLE_S:
            return None
        try:
            reply = self.client().position(pos["product_id"])
        except Exception:
            return None
        return position_size(reply, pos["product_id"], pos.get("symbol"))

    def _closed_outside(self, pos, net):
        held = self._held(pos)
        net = max(0, net)
        if net < held:
            pos["outside_sold"] = int(pos.get("outside_sold") or 0) + held - net
        if self._held(pos) <= 0:
            pos["state"] = "closed"
            pos.setdefault("exit_reason", "it was closed outside the tool")
            pos["outside_unconfirmed"] = True          # looked at once more when the ticket closes (_reconsider)
            self._note(pos["index"], "Delta showed none held twice in a row, so the position looks closed outside the "
                                     "tool (on Delta's own screen?). When the ticket closes the tool will look once "
                                     "more and still sell if anything is there.", "warn", pos)
            return
        self._note(pos["index"], f"Part of the position was sold outside the tool; {self._held(pos)} still held.", "warn", pos)

    def _sell_rest(self, pos):
        if pos.get("exit_order_id"):
            return
        rest = self._held(pos)
        if rest > 0:
            net = self._net_qty(pos)
            # A reading of zero is not trusted to cancel the sell: it has been wrong (the reply
            # is not always shaped as expected). The sell is reduce-only, so it cannot open a
            # position; Delta refusing it, or it filling nothing, is what settles it (FLAT_REFUSALS).
            pos["net_zero_at_exit"] = net == 0
            if net is not None and 0 < net < rest:
                pos["outside_sold"] = int(pos.get("outside_sold") or 0) + rest - net
                self._note(pos["index"], f"Delta shows {net} held, not {rest} - selling only what is held.",
                           "warn", pos)
                rest = self._held(pos)
        if rest <= 0:
            pos["state"] = "closed"
            self._note(pos["index"], f"Nothing left to sell ({pos.get('exit_reason')}). Position closed.", pos=pos)
            return
        pos["exit_attempts"] = pos.get("exit_attempts", 0) + 1
        try:
            if pos["exit_attempts"] > LIMIT_ATTEMPTS:
                pos["exit_order_id"] = self._place(pos, size=rest, side="sell", order_type="market_order",
                                                   reduce_only=True, client_order_id=pos["tag"] + "X")
                pos.update(state="exiting", exit_at=self.clock(), exit_limit=None)
                self._note(pos["index"], f"SELL {rest} at market (reduce-only) sent after {LIMIT_ATTEMPTS} limits did "
                                         f"not fill - {pos.get('exit_reason')} (order {pos['exit_order_id']}).", "warn", pos)
                return
            mark = self._mark(pos) or pos.get("avg_price") or pos.get("stop_trigger")
            limit = _floor_tick(float(mark) * (1 - EXIT_BUFFER), pos.get("tick", 0.1))
            pos["exit_order_id"] = self._place(pos, size=rest, side="sell", order_type="limit_order",
                                               limit_price=str(limit), reduce_only=True,
                                               client_order_id=pos["tag"] + f"X{pos['exit_attempts']}")
            pos.update(state="exiting", exit_at=self.clock(), exit_limit=limit)
            self._note(pos["index"], f"SELL {rest} limit {limit} sent - {pos.get('exit_reason')} "
                                     f"(order {pos['exit_order_id']}).", pos=pos)
        except Exception as exc:
            pos["state"] = "exiting"
            pos["exit_order_id"] = None
            pos["exit_at"] = self.clock()
            pos["refusals"] = int(pos.get("refusals") or 0) + 1
            if pos.get("net_zero_at_exit") and pos["refusals"] >= FLAT_REFUSALS and not _infra_error(exc):
                return self._gone(pos, f"Delta refused the sell ({exc}) and shows none held")
            self._note(pos["index"], f"Sell order refused: {exc} - retrying.", "error", pos)

    def _check_exit(self, pos):
        oid = pos.get("exit_order_id")
        if not oid:
            if self.clock() - pos.get("exit_at", 0) >= REPRICE_S:
                self._sell_rest(pos)
            return
        state, filled, avg = _order_state(self._order(oid))
        done_before = int(pos.get("exit_done_of", 0))
        if state in ("closed", "cancelled"):
            new = max(0, filled - done_before)
            pos["exit_filled"] = int(pos.get("exit_filled") or 0) + new
            if avg and new:
                pos["exit_price"] = avg
                pos["exit_value"] = float(pos.get("exit_value") or 0) + new * avg
                pos["exit_priced"] = int(pos.get("exit_priced") or 0) + new
            pos["exit_order_id"] = None
            pos["exit_done_of"] = 0
            if self._held(pos) <= 0:
                pos["state"] = "closed"
                self._note(pos["index"], f"Sold at {pos.get('exit_price')}. Position closed.", pos=pos)
                return
            if new == 0 and pos.get("net_zero_at_exit"):
                pos["zero_fills"] = int(pos.get("zero_fills") or 0) + 1
                if pos["zero_fills"] >= FLAT_REFUSALS:
                    return self._gone(pos, "The sell filled nothing and Delta shows none held")
            return self._sell_rest(pos)
        if self.clock() - pos.get("exit_at", 0) >= REPRICE_S:
            # A limit that has not filled: cancel it and go again a little lower
            # (or at market after LIMIT_ATTEMPTS). What it did fill is banked.
            try:
                self.client().cancel_order(oid, pos["product_id"])
            except Exception:
                pass
            state, filled, avg = _order_state(self._order(oid))
            pos["exit_filled"] = int(pos.get("exit_filled") or 0) + filled
            if avg and filled:
                pos["exit_price"] = avg
                pos["exit_value"] = float(pos.get("exit_value") or 0) + filled * avg
                pos["exit_priced"] = int(pos.get("exit_priced") or 0) + filled
            pos["exit_order_id"] = None
            pos["exit_at"] = self.clock()
            if self._held(pos) <= 0:
                pos["state"] = "closed"
                self._note(pos["index"], f"Sold at {pos.get('exit_price')}. Position closed.", pos=pos)
                return
            if filled == 0 and pos.get("net_zero_at_exit"):
                pos["zero_fills"] = int(pos.get("zero_fills") or 0) + 1
                if pos["zero_fills"] >= FLAT_REFUSALS:
                    return self._gone(pos, "The sell filled nothing and Delta shows none held")
            self._sell_rest(pos)

    # ------------------------------------------------------------ real fills
    def _log_fills(self):
        path = fills_path(self.log_path)
        if not path:
            return
        wrote = False
        for pos in list(self.positions.values()):
            if pos.get("state") != "closed" or pos.get("fills_logged"):
                continue
            bought = int(pos.get("filled_qty") or 0)
            if bought <= 0 or pos.get("avg_price") is None:
                pos["fills_logged"] = True
                continue
            priced = int(pos.get("exit_priced") or 0)
            exit_avg = round(float(pos.get("exit_value") or 0) / priced, 4) if priced else None
            cv = float(pos.get("contract_value") or 0.001)
            row = {"trade_id": pos.get("trade_id"), "index": pos.get("index"), "source": pos.get("source", "rule"),
                   "day": pos.get("day"), "contract": pos.get("symbol"), "qty": bought,
                   "entry_avg": pos.get("avg_price"), "paper_entry": pos.get("paper_entry"),
                   "exit_avg": exit_avg, "exit_qty_priced": priced,
                   "sold_outside_qty": int(pos.get("outside_sold") or 0),
                   "gross_pnl": (round((exit_avg - float(pos["avg_price"])) * priced * cv, 4)
                                 if exit_avg is not None else None),
                   "exit_reason": pos.get("exit_reason"), "logged": self.now().isoformat()}
            try:
                with open(path, "a") as fh:
                    fh.write(json.dumps(row, default=str) + "\n")
                pos["fills_logged"] = True
                wrote = True
            except OSError:
                pass
        if wrote:
            self._save()

    def fills(self, source=None):
        return read_fills(self.log_path, source)

    # ------------------------------------------------------------ the loop's work
    def poll(self):
        self._log_fills()
        active = [p for p in list(self.positions.values()) if p.get("state") in ACTIVE]
        if not active:
            return
        for pos in active:
            try:
                state = pos.get("state")
                if state == "entering":
                    self._check_entry(pos)
                elif state == "open":
                    self._check_open(pos)
                elif state == "exiting":
                    self._check_exit(pos)
                elif state == "placing":
                    self._recover_placing(pos)
            except Exception as exc:
                msg = f"Could not check the order: {exc}"
                last = pos.get("last_err") or ["", 0]
                if msg != last[0] or self.clock() - last[1] >= 60:
                    pos["last_err"] = [msg, self.clock()]
                    self._note(pos["index"], msg, "error", pos)
        self._save()

    def _recover_placing(self, pos):
        try:
            rows = self.client().open_orders(pos["product_id"]) or []
        except Exception:
            return
        mine = [o for o in rows if o.get("client_order_id") == pos["tag"] and o.get("side") == "buy"]
        if not mine:
            if self.clock() - pos.get("placing_at", 0) < PLACING_WAIT_S:
                return
            pos["state"] = "failed"
            self._note(pos["index"], "Delta has no record of the entry that went unanswered - nothing bought. "
                                     "The ticket carries on as a paper ticket.", "warn", pos)
            return
        pos["entry_order_id"] = str(mine[-1]["id"])
        pos["state"], pos["entry_at"] = "entering", self.clock()
        if pos.get("restarted") or pos.get("ticket_closed"):
            pos["entry_at"] = 0
            self._exit(pos, pos.get("ticket_closed") or "the tool restarted while this entry was being placed")
        else:
            self._note(pos["index"], f"The entry did reach Delta (order {pos['entry_order_id']}).", "warn", pos)

    def _recover(self):
        self.recover_all("the tool restarted while this position was open")

    def recover_all(self, reason):
        with self.lock:
            for pos in self.positions.values():
                if pos.get("state") == "placing":
                    pos["restarted"] = True
                elif pos.get("state") in ("entering", "open") or (pos.get("state") == "closed" and pos.get("outside_unconfirmed")):
                    self.q.put(("recover", {"trade_id": pos["trade_id"]}, {"reason": reason}))

    # ------------------------------------------------------------ reading
    def public(self):
        with self.lock:
            today = self._today()
            pos = [{k: p.get(k) for k in ("trade_id", "index", "state", "source", "symbol", "qty", "filled_qty",
                                          "avg_price", "stop_trigger", "mark", "exit_price", "exit_reason",
                                          "entry_order_id")} for p in self.positions.values() if p.get("day") == today]
            for p in pos:
                p["tradingsymbol"] = p.get("symbol")        # the page reads the Zerodha executor's name for it
            pos.sort(key=lambda p: p.get("trade_id") or "", reverse=True)
            return {"enabled": dict(self.enabled), "enabled_ai": dict(self.enabled_ai), "venue": "Delta Exchange India",
                    "stop_at_venue": False, "positions": pos, "notes": self.notes[:8],
                    "entries_today": self.entries_today if self.day == today else 0,
                    "max_entries": MAX_ENTRIES_PER_DAY}
