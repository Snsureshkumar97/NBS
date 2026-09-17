"""
live_orders.py — real Zerodha orders that follow a ticket
================================================================================
Asked for by the user on 17 Sep 2026: a switch per Indian index that makes the
tool take each ticket as a real trade, with the ticket's own targets and stop.
The user chose intraday (MIS) positions and to go live without a dry run.

WHAT A TICKET BECOMES, WHEN ITS INDEX IS SWITCHED ON
    1. BUY the ticket's exact contract (strike, type, expiry), lots x the lot
       size Zerodha lists - never the lot size in config - as a LIMIT order a
       little above the last price. Anything unfilled after FILL_WAIT_S is
       cancelled and only what filled is carried.
    2. The moment it fills, a STOP-LOSS LIMIT sell sits at Zerodha on the
       ticket's frozen stop. Zerodha does not accept stop-loss MARKET orders on
       index options, and a stop that lives at the exchange still works when
       this Mac is asleep, offline or restarting. If the stop cannot be placed
       the position is sold at once - it is never left unprotected.
    3. The ticket closes on T2 (or the stop, or Clear ticket): the stop order is
       cancelled and whatever is still held is sold with a LIMIT a little below
       the last price, repriced every REPRICE_S until it fills.
    4. At SQUARE_OFF (15:20), anything still open is closed, ticket and all,
       ahead of Zerodha's own intraday square-off around 15:25.

WHAT IS NEVER DONE
    No order for a ticket priced off the index rather than a live premium, for
    an index that was switched off when the ticket opened, from a second
    ticket on an index that already holds a position, after NO_ENTRY_AFTER, or
    beyond MAX_ENTRIES_PER_DAY. Every order is for this account only, with its
    own Zerodha token, and every step is written to <trade log>.live.json.

RESTARTS
    A restart closes every ticket in the record ("the tool stopped"), so a real
    position found open on start is sold too - the record and the account must
    not disagree about whether you are in a trade.

*** Zerodha rejects API orders from an IP that is not registered on the
*** developer console (SEBI, from 1 Apr 2026). That rejection is shown as-is.
"""
import datetime as dt
import json
import math
import os
import queue
import threading
import time

import config
from main import now_ist

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
EXCHANGE = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}
PRODUCT = "MIS"
SQUARE_OFF = (15, 20)
NO_ENTRY_AFTER = (15, 10)
ENTRY_BUFFER = 0.02
EXIT_BUFFER = 0.03
STOP_LIMIT_GAP = 0.05
FILL_WAIT_S = 20
REPRICE_S = 5
MAX_EXIT_ATTEMPTS = 6
MAX_ENTRIES_PER_DAY = 10
VERIFY_S = 15                # how often an open position is checked against Zerodha's own positions
SETTLE_S = 10                # positions can lag a fill by a moment; not trusted to shrink a holding sooner
PLACING_WAIT_S = 15          # how long an unanswered entry is looked for before it is taken as never sent
POLL_S = 1.0
NOTES_KEPT = 30

ACTIVE = ("placing", "entering", "open", "exiting", "attention")

_registry = {}
_registry_lock = threading.Lock()


def for_account(email, log_path, close_ticket, kite=None):
    """The one executor for an account. A feed is rebuilt when it goes idle and
    comes back; a second executor on the same positions could sell them twice,
    so the existing one is handed the new feed instead. That new feed's ticket
    book starts empty, so anything still held is sold, as on a restart."""
    key = (email or "").strip().lower()
    with _registry_lock:
        ex = _registry.get(key)
        if ex is None:
            ex = _registry[key] = Executor(email, log_path, kite=kite, close_ticket=close_ticket)
            return ex
    ex.close_ticket = close_ticket
    ex.recover_all("the tool's ticket tracking restarted while this position was open")
    return ex


def _floor_tick(x, tick):
    return round(max(tick, math.floor(x / tick + 1e-9) * tick), 2)


def _ceil_tick(x, tick):
    return round(math.ceil(x / tick - 1e-9) * tick, 2)


def _unanswered(exc):
    name = type(exc).__name__
    return name in ("NetworkException", "Timeout", "ReadTimeout", "ConnectionError", "ConnectTimeout")


def _kite_factory(email):
    def make():
        from kiteconnect import KiteConnect
        import user_kite
        token = user_kite.token_for(email)
        if not token:
            raise RuntimeError("Zerodha is not connected for this account today.")
        k = KiteConnect(api_key=config.KITE_API_KEY)
        k.set_access_token(token)
        return k
    return make


class Executor:
    """One account's live orders on the Indian indices."""

    def __init__(self, email, log_path, kite=None, close_ticket=None, now=None, clock=None, start=True):
        self.email = email
        self.path = (log_path + ".live.json") if log_path else None
        self.make_kite = kite or _kite_factory(email)
        self.close_ticket = close_ticket or (lambda index, status: None)
        self.now = now or now_ist
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.q = queue.Queue()
        # Two switches per index, both off: the rule tickets' own, and the AI
        # desk's. An AI ticket is paper unless its own switch here is on.
        self.enabled = {k: False for k in INDICES}
        self.enabled_ai = {k: False for k in INDICES}
        self.positions = {}          # trade_id -> position
        self.notes = []              # newest first
        self.day = None
        self.entries_today = 0
        self._instruments = {}       # (exchange, day) -> rows
        self._kite = None
        self._kite_day = None
        self._load()
        self._recover()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._loop, daemon=True, name=f"live:{email}")
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
            keep = {t: p for t, p in self.positions.items()
                    if p.get("day") == today or p.get("state") in ACTIVE}
            self.positions = keep
            data = {"enabled": self.enabled, "enabled_ai": self.enabled_ai,
                    "positions": keep, "notes": self.notes[:NOTES_KEPT],
                    "day": self.day, "entries_today": self.entries_today}
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
        print(f"[live] {self.now():%Y-%m-%d %H:%M:%S} {index}: {text}", flush=True)

    # ------------------------------------------------------------ the switch
    def switches(self, source="rule"):
        return self.enabled_ai if source == "ai" else self.enabled

    def set_enabled(self, index, on, source="rule"):
        if index not in INDICES:
            raise ValueError("That index cannot place live orders.")
        if source not in ("rule", "ai"):
            raise ValueError("Unknown kind of ticket.")
        with self.lock:
            self.switches(source)[index] = bool(on)
            self._save()
        what = "AI trades on " + index if source == "ai" else index
        self._note(index, f"Live orders for {what} switched "
                          + ("ON." if on else "OFF. An open position is still managed to its exit."))
        if on:
            self.q.put(("warm", {}, {}))
        return self.switches(source)[index]

    # ------------------------------------------------------------ ticket events
    def on_ticket_event(self, kind, trade, source="rule", **info):
        """Called from inside a ticket engine's lock: only queue, never wait.
        `source` says whose ticket it is - the rules' or the AI desk's - and each
        has its own switch and its own live position per index."""
        if not trade or trade.get("index") not in INDICES:
            return
        info = dict(info, source=source)
        if kind == "opened":
            self.q.put(("open", dict(trade), info))
        elif kind == "closed":
            self.q.put(("close", {"trade_id": trade.get("trade_id"), "index": trade.get("index"),
                                  "status": trade.get("status")}, info))

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
                self._note("-", f"Unexpected error in the live-order loop: {type(exc).__name__}: {exc}", "error")

    def handle(self, what, trade, info):
        if what == "open":
            self._enter(trade, (info or {}).get("source") or "rule")
        elif what == "warm":
            for index in INDICES:
                if self.enabled.get(index) or self.enabled_ai.get(index):
                    self._rows(EXCHANGE[index])
        else:
            pos = self.positions.get(trade.get("trade_id"))
            if pos is not None:
                reason = (f"the ticket closed ({trade.get('status') or 'closed'})" if what == "close"
                          else info.get("reason") or "the tool restarted while this position was open")
                # Remembered for an entry whose fate is not known yet: when it
                # turns out to have reached Zerodha, it is sold, not protected.
                pos["ticket_closed"] = reason
                self._exit(pos, reason)
        self._save()

    # ------------------------------------------------------------ kite
    def kite(self):
        today = self._today()
        if self._kite is None or self._kite_day != today:
            self._kite, self._kite_day = self.make_kite(), today
        return self._kite

    def _rows(self, exch):
        key = (exch, self._today())
        rows = self._instruments.get(key)
        if rows is None:
            rows = self._instruments[key] = [
                r for r in self.kite().instruments(exch)
                if r.get("name") in INDICES and r.get("instrument_type") in ("CE", "PE")]
            for old in [k for k in self._instruments if k[1] != key[1]]:
                del self._instruments[old]
        return rows

    def _contract(self, index, strike, option_type, expiry):
        rows = [r for r in self._rows(EXCHANGE[index]) if r.get("name") == index]
        want = str(expiry or "")[:10]
        for r in rows:
            if (str(r.get("expiry"))[:10] == want and r.get("instrument_type") == option_type
                    and float(r.get("strike") or 0) == float(strike)):
                return r
        return None

    def _ltp(self, pos):
        try:
            key = f"{pos['exchange']}:{pos['tradingsymbol']}"
            return float(self.kite().ltp([key])[key]["last_price"])
        except Exception:
            return None

    def _history(self, order_id):
        h = self.kite().order_history(order_id) or []
        return h[-1] if h else {}

    def _place(self, pos, **kw):
        return str(self.kite().place_order(
            variety="regular", exchange=pos["exchange"], tradingsymbol=pos["tradingsymbol"],
            product=PRODUCT, validity="DAY", tag=pos["tag"], **kw))

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
            if any(p.get("index") == index and p.get("source", "rule") == source
                   and p.get("state") in ACTIVE for p in self.positions.values()):
                self._note(index, f"No live order: this index already holds a live {who}position.", "warn")
                return
            if (now.hour, now.minute) >= NO_ENTRY_AFTER:
                self._note(index, f"No live order: tickets after {NO_ENTRY_AFTER[0]}:{NO_ENTRY_AFTER[1]:02d} "
                                  "stay on paper - too close to the 15:20 close.", "warn")
                return
            if self.entries_today >= MAX_ENTRIES_PER_DAY:
                self._note(index, f"No live order: {MAX_ENTRIES_PER_DAY} live entries today is the hard cap.", "warn")
                return
            if not trade.get("use_premium") or trade.get("premium_sl") is None:
                self._note(index, "No live order: this ticket is priced off the index, not a live option "
                                  "premium, so its stop is not a premium level.", "warn")
                return
            pos = self.positions[tid] = {
                "trade_id": tid, "index": index, "day": today, "state": "placing", "source": source,
                "strike": trade.get("strike"), "option_type": trade.get("option_type"),
                "expiry": str(trade.get("expiry") or "")[:10], "lots": trade.get("lots") or 1,
                "stop_trigger": trade.get("premium_sl"), "t2": (trade.get("premium_targets") or [None, None])[1],
                "tag": ("TP" + index[:2] + now.strftime("%H%M%S")),
                "entry_order_id": None, "stop_order_id": None, "exit_order_id": None,
                "filled_qty": 0, "avg_price": None, "stop_filled": 0, "exit_filled": 0,
                "exit_attempts": 0, "created": now.isoformat(), "log": [],
            }
            self.entries_today += 1
            self._save()
        try:
            inst = self._contract(index, pos["strike"], pos["option_type"], pos["expiry"])
            if not inst:
                return self._fail(pos, f"Zerodha lists no {index} {pos['strike']} {pos['option_type']} "
                                       f"expiring {pos['expiry']} - no order placed.")
            lots = int(float(pos["lots"]))
            lot_size = int(inst["lot_size"])
            pos.update(tradingsymbol=inst["tradingsymbol"], exchange=EXCHANGE[index],
                       tick=float(inst.get("tick_size") or 0.05), lot_size=lot_size, qty=lots * lot_size)
            if lots < 1:
                return self._fail(pos, "No live order: the ticket is for less than one lot.")
            ltp = self._ltp(pos) or trade.get("entry_ltp")
            if not ltp:
                return self._fail(pos, "No live order: no live price for the contract.")
            limit = _ceil_tick(float(ltp) * (1 + ENTRY_BUFFER), pos["tick"])
            pos["entry_limit"] = limit
            pos["sending"] = True
            pos["entry_order_id"] = self._place(pos, transaction_type="BUY", quantity=pos["qty"],
                                                order_type="LIMIT", price=limit)
            pos["state"], pos["entry_at"] = "entering", self.clock()
            self._note(index, f"{who}ticket: BUY {pos['qty']} {pos['tradingsymbol']} limit {limit} sent "
                              f"(order {pos['entry_order_id']}).", pos=pos)
        except Exception as exc:
            if pos.get("sending") and not pos.get("entry_order_id") and _unanswered(exc):
                # It may have reached Zerodha. Look for it by its tag rather than
                # guess either way.
                pos["state"], pos["placing_at"] = "placing", self.clock()
                self._note(index, f"Zerodha did not answer the entry ({self._why(exc)}) - checking "
                                  "whether it arrived.", "warn", pos)
            else:
                self._fail(pos, f"Entry order refused: {self._why(exc)}")
        finally:
            self._save()

    def _fail(self, pos, text):
        pos["state"] = "failed"
        self._note(pos["index"], text + " The ticket carries on as a paper ticket.", "error", pos)
        self._save()

    def _why(self, exc):
        if type(exc).__name__ == "TokenException":
            self._kite = None
        msg = str(exc) or type(exc).__name__
        if " IP" in f" {msg}" or "whitelist" in msg.lower():
            msg += " (Is this connection's static IP registered on developers.kite.trade?)"
        return msg

    def _check_entry(self, pos):
        h = self._history(pos["entry_order_id"])
        status = h.get("status")
        pos["filled_qty"] = int(h.get("filled_quantity") or 0)
        if h.get("average_price"):
            pos["avg_price"] = float(h["average_price"])
        if status == "COMPLETE":
            return self._protect(pos)
        if status in ("REJECTED", "CANCELLED"):
            if pos["filled_qty"] > 0:
                return self._protect(pos)
            return self._fail(pos, f"Entry order {status.lower()} by Zerodha: {h.get('status_message') or 'no reason given'}.")
        if self.clock() - pos.get("entry_at", 0) >= FILL_WAIT_S:
            try:
                self.kite().cancel_order(variety="regular", order_id=pos["entry_order_id"])
            except Exception:
                pass
            h = self._history(pos["entry_order_id"])
            pos["filled_qty"] = int(h.get("filled_quantity") or 0)
            if h.get("average_price"):
                pos["avg_price"] = float(h["average_price"])
            if pos["filled_qty"] > 0:
                self._note(pos["index"], f"Entry only partly filled in {FILL_WAIT_S}s: carrying "
                                         f"{pos['filled_qty']} of {pos['qty']}.", "warn", pos)
                return self._protect(pos)
            return self._fail(pos, f"Entry not filled within {FILL_WAIT_S}s at {pos['entry_limit']} or better "
                                   "- cancelled, nothing bought.")

    def _protect(self, pos):
        """Put the stop at Zerodha. A position that cannot be protected is sold."""
        trig = _floor_tick(float(pos["stop_trigger"]), pos["tick"])
        limit = _floor_tick(trig * (1 - STOP_LIMIT_GAP), pos["tick"])
        self._note(pos["index"], f"Bought {pos['filled_qty']} at {pos['avg_price']}.", pos=pos)
        pos["filled_at"] = pos["verified_at"] = self.clock()
        if pos.get("ticket_closed"):
            return self._exit(pos, pos["ticket_closed"])
        if pos["avg_price"] is not None and pos["avg_price"] <= trig:
            return self._exit(pos, "it filled at or below the stop")
        try:
            pos["stop_order_id"] = self._place(pos, transaction_type="SELL", quantity=pos["filled_qty"],
                                               order_type="SL", trigger_price=trig, price=limit)
            pos.update(state="open", stop_limit=limit, stop_trigger=trig)
            self._note(pos["index"], f"Stop-loss order at Zerodha: trigger {trig}, limit {limit} "
                                     f"(order {pos['stop_order_id']}).", pos=pos)
        except Exception as exc:
            self._note(pos["index"], f"Stop-loss order refused: {self._why(exc)} - selling now rather than "
                                     "hold an unprotected position.", "error", pos)
            self._exit(pos, "the stop-loss order could not be placed")

    # ------------------------------------------------------------ holding
    def _check_open(self, pos):
        now = self.now()
        if (now.hour, now.minute) >= SQUARE_OFF:
            try:
                self.close_ticket(pos["index"], "CLOSED — intraday close at 15:20 (live order)")
            except Exception:
                pass                 # the record can lag; the account must not
            return self._exit(pos, "it is 15:20, the intraday close")
        if self.clock() - pos.get("verified_at", 0) >= VERIFY_S:
            pos["verified_at"] = self.clock()
            net = self._net_qty(pos)
            if net is not None and net < self._held(pos):
                return self._closed_outside(pos, net)
        h = self._history(pos["stop_order_id"])
        status = h.get("status")
        pos["stop_filled"] = int(h.get("filled_quantity") or 0)
        if status == "COMPLETE":
            pos["state"] = "closed"
            pos["exit_price"] = float(h.get("average_price") or 0) or None
            self._note(pos["index"], f"Stop-loss order filled at Zerodha at {pos['exit_price']}. Position closed.",
                       "warn", pos)
            return
        if status == "OPEN":
            # Triggered, but the limit below it has not filled: price fell through.
            pos.setdefault("stop_open_at", self.clock())
            if self.clock() - pos["stop_open_at"] >= REPRICE_S:
                return self._exit(pos, "the stop triggered but its limit did not fill")
            return
        if status in ("REJECTED", "CANCELLED"):
            self._note(pos["index"], f"The stop-loss order was {status.lower()} "
                                     f"({h.get('status_message') or 'no reason given'}) - selling rather than "
                                     "hold an unprotected position.", "error", pos)
            return self._exit(pos, "the stop-loss order is gone")

    # ------------------------------------------------------------ exit
    def _exit(self, pos, reason):
        """Cancel what is working, then sell whatever is still held. Only ever
        from entering or open: an exit already under way is never started twice."""
        if pos.get("state") not in ("entering", "open"):
            return
        k = self.kite()
        if pos.get("state") == "entering" and pos.get("entry_order_id"):
            try:
                k.cancel_order(variety="regular", order_id=pos["entry_order_id"])
            except Exception:
                pass
            h = self._history(pos["entry_order_id"])
            pos["filled_qty"] = int(h.get("filled_quantity") or 0)
        if pos.get("stop_order_id"):
            h = self._history(pos["stop_order_id"])
            if h.get("status") in ("TRIGGER PENDING", "OPEN"):
                try:
                    k.cancel_order(variety="regular", order_id=pos["stop_order_id"])
                except Exception:
                    pass
                h = self._history(pos["stop_order_id"])
            pos["stop_filled"] = int(h.get("filled_quantity") or 0)
        pos["exit_reason"] = reason
        self._sell_rest(pos)

    def _held(self, pos):
        return (int(pos.get("filled_qty") or 0) - int(pos.get("stop_filled") or 0)
                - int(pos.get("exit_filled") or 0) - int(pos.get("outside_sold") or 0))

    def _net_qty(self, pos):
        """This contract's intraday quantity at Zerodha right now, or None if it
        cannot be read. Never trusted in the first SETTLE_S after the fill."""
        if self.clock() - pos.get("filled_at", 0) < SETTLE_S:
            return None
        try:
            net = (self.kite().positions() or {}).get("net") or []
        except Exception:
            return None
        return sum(int(p.get("quantity") or 0) for p in net
                   if p.get("tradingsymbol") == pos.get("tradingsymbol") and p.get("product") == PRODUCT)

    def _closed_outside(self, pos, net):
        """Some or all of the position was sold outside the tool - in Kite, say.
        Its stop must not be left to sell what is no longer held."""
        held = self._held(pos)
        k = self.kite()
        if pos.get("stop_order_id"):
            try:
                k.cancel_order(variety="regular", order_id=pos["stop_order_id"])
            except Exception:
                pass
            h = self._history(pos["stop_order_id"])
            pos["stop_filled"] = int(h.get("filled_quantity") or 0)
            held = self._held(pos)
        net = max(0, net)
        if net < held:
            pos["outside_sold"] = int(pos.get("outside_sold") or 0) + held - net
        if self._held(pos) <= 0:
            pos["state"] = "closed"
            self._note(pos["index"], "The position was closed outside the tool (in Kite?). Its stop-loss "
                                     "order is cancelled so it cannot sell what is no longer held.", "warn", pos)
            return
        self._note(pos["index"], f"Part of the position was sold outside the tool; {self._held(pos)} still "
                                 "held. Re-placing the stop for that.", "warn", pos)
        trig, limit = pos["stop_trigger"], pos.get("stop_limit") or _floor_tick(
            float(pos["stop_trigger"]) * (1 - STOP_LIMIT_GAP), pos["tick"])
        remaining = self._held(pos)
        try:
            pos["stop_order_id"] = self._place(pos, transaction_type="SELL", quantity=remaining,
                                               order_type="SL", trigger_price=trig, price=limit)
            # A new stop order counts its own fills from zero; what the old one
            # sold is folded into filled_qty so the holding stays the same number.
            pos["filled_qty"] = remaining + int(pos.get("exit_filled") or 0) + int(pos.get("outside_sold") or 0)
            pos["stop_filled"] = 0
        except Exception as exc:
            self._note(pos["index"], f"Stop-loss order refused: {self._why(exc)} - selling the rest.", "error", pos)
            self._exit(pos, "the stop-loss order could not be re-placed")

    def _sell_rest(self, pos):
        if pos.get("exit_order_id"):
            return                   # one sell working at a time, always
        rest = self._held(pos)
        if rest > 0:
            net = self._net_qty(pos)
            if net is not None and net < rest:
                pos["outside_sold"] = int(pos.get("outside_sold") or 0) + rest - max(0, net)
                self._note(pos["index"], f"Zerodha shows {max(0, net)} held, not {rest} - selling only "
                                         "what is actually held.", "warn", pos)
                rest = self._held(pos)
        if rest <= 0:
            pos["state"] = "closed"
            self._note(pos["index"], f"Nothing left to sell ({pos.get('exit_reason')}). Position closed.", pos=pos)
            return
        if pos.get("exit_attempts", 0) >= MAX_EXIT_ATTEMPTS:
            if pos.get("state") != "attention":
                pos["state"] = "attention"
                self._note(pos["index"], f"{rest} still held after {MAX_EXIT_ATTEMPTS} sell attempts - "
                                         "CHECK KITE NOW. Zerodha squares off intraday positions around 15:25.",
                           "error", pos)
            return
        ltp = self._ltp(pos)
        if ltp is None:
            ltp = pos.get("avg_price") or pos.get("stop_trigger")
        limit = _floor_tick(float(ltp) * (1 - EXIT_BUFFER), pos.get("tick", 0.05))
        pos["exit_attempts"] = pos.get("exit_attempts", 0) + 1
        try:
            pos["exit_order_id"] = self._place(pos, transaction_type="SELL", quantity=rest,
                                               order_type="LIMIT", price=limit)
            pos.update(state="exiting", exit_at=self.clock(), exit_limit=limit)
            self._note(pos["index"], f"SELL {rest} limit {limit} sent - {pos.get('exit_reason')} "
                                     f"(order {pos['exit_order_id']}).", pos=pos)
        except Exception as exc:
            pos["state"] = "exiting"
            pos["exit_order_id"] = None
            pos["exit_at"] = self.clock()
            self._note(pos["index"], f"Sell order refused: {self._why(exc)} - retrying.", "error", pos)

    def _check_exit(self, pos):
        oid = pos.get("exit_order_id")
        if not oid:
            if self.clock() - pos.get("exit_at", 0) >= REPRICE_S:
                self._sell_rest(pos)
            return
        h = self._history(oid)
        status, filled = h.get("status"), int(h.get("filled_quantity") or 0)
        if status == "COMPLETE" or (status in ("REJECTED", "CANCELLED")):
            pos["exit_filled"] = int(pos.get("exit_filled") or 0) + filled
            if h.get("average_price") and filled:
                pos["exit_price"] = float(h["average_price"])
            pos["exit_order_id"] = None
            if status != "COMPLETE":
                self._note(pos["index"], f"Sell order {status.lower()}: {h.get('status_message') or 'no reason given'}.",
                           "error", pos)
            if self._held(pos) <= 0:
                pos["state"] = "closed"
                self._note(pos["index"], f"Sold at {pos.get('exit_price')}. Position closed.", pos=pos)
                return
            return self._sell_rest(pos)
        if self.clock() - pos.get("exit_at", 0) >= REPRICE_S:
            ltp = self._ltp(pos)
            if ltp is None:
                return
            limit = _floor_tick(ltp * (1 - EXIT_BUFFER), pos.get("tick", 0.05))
            try:
                self.kite().modify_order(variety="regular", order_id=oid, price=limit)
                pos.update(exit_at=self.clock(), exit_limit=limit)
                self._note(pos["index"], f"Sell not filled yet - repriced to {limit}.", pos=pos)
            except Exception as exc:
                self._note(pos["index"], f"Could not reprice the sell: {self._why(exc)}.", "error", pos)
                pos["exit_at"] = self.clock()

    # ------------------------------------------------------------ the loop's work
    def poll(self):
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
                elif state == "exiting" or (state == "attention" and pos.get("exit_order_id")):
                    self._check_exit(pos)
                elif state == "placing":
                    self._recover_placing(pos)
            except Exception as exc:
                msg = f"Could not check the order: {self._why(exc)}"
                last = pos.get("last_err") or ["", 0]
                if msg != last[0] or self.clock() - last[1] >= 60:
                    pos["last_err"] = [msg, self.clock()]
                    self._note(pos["index"], msg, "error", pos)
        self._save()

    def _recover_placing(self, pos):
        """The entry was being sent when the process stopped: find it by its tag."""
        orders = [o for o in (self.kite().orders() or []) if o.get("tag") == pos["tag"]
                  and o.get("transaction_type") == "BUY"]
        if not orders:
            if self.clock() - pos.get("placing_at", 0) < PLACING_WAIT_S:
                return               # an order Zerodha took can take a moment to list
            pos["state"] = "failed"
            self._note(pos["index"], "Zerodha has no record of the entry that went unanswered - nothing bought. "
                                     "The ticket carries on as a paper ticket.", "warn", pos)
            return
        pos["entry_order_id"] = str(orders[-1]["order_id"])
        pos["state"], pos["entry_at"] = "entering", self.clock()
        if pos.get("restarted") or pos.get("ticket_closed"):
            pos["entry_at"] = 0
            self._exit(pos, pos.get("ticket_closed") or "the tool restarted while this entry was being placed")
        else:
            self._note(pos["index"], f"The entry did reach Zerodha (order {pos['entry_order_id']}).", "warn", pos)

    def _recover(self):
        """Positions left from a stopped process. Their tickets are gone, so what
        is still held today is sold."""
        today = self._today()
        for pos in self.positions.values():
            if pos.get("state") in ACTIVE and pos.get("day") != today:
                pos["state"] = "closed"
                pos.setdefault("log", []).append({"at": "", "text": "From an earlier session - intraday "
                                                  "positions are squared off by Zerodha at the close."})
        self.recover_all("the tool restarted while this position was open")
        if any(self.enabled.values()) or any(self.enabled_ai.values()):
            self.q.put(("warm", {}, {}))

    def recover_all(self, reason):
        with self.lock:
            for pos in self.positions.values():
                if pos.get("state") == "placing":
                    pos["restarted"] = True
                elif pos.get("state") in ("entering", "open"):
                    self.q.put(("recover", {"trade_id": pos["trade_id"]}, {"reason": reason}))

    # ------------------------------------------------------------ reading
    def public(self):
        with self.lock:
            today = self._today()
            pos = [{k: p.get(k) for k in ("trade_id", "index", "state", "source", "tradingsymbol", "qty", "filled_qty",
                                          "avg_price", "stop_trigger", "stop_limit", "exit_price",
                                          "exit_reason", "entry_order_id", "stop_order_id")}
                   for p in self.positions.values() if p.get("day") == today]
            pos.sort(key=lambda p: p.get("trade_id") or "", reverse=True)
            return {"enabled": dict(self.enabled), "enabled_ai": dict(self.enabled_ai),
                    "positions": pos, "notes": self.notes[:8],
                    "entries_today": self.entries_today if self.day == today else 0,
                    "max_entries": MAX_ENTRIES_PER_DAY}
