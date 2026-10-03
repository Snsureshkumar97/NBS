"""
exness_orders.py — real orders for Bitcoin and gold tickets, on the user's own Exness account
================================================================================
The user, 3 Oct 2026: "when i hit live trades it should ask demo or real account and it should
trades from those accounts". The twin of delta_orders.py / live_orders.py for the Exness market:
switched on per instrument (BTC, GOLD) and per kind of ticket (the rules' own, the AI desk's),
off by default, and each switch names ONE of the user's connected Exness accounts
(user_exness.py) - a DEMO or a REAL one. A REAL account is only accepted with an explicit
confirmation (web_server._do_live), and an account connected with MetaApi's investor
(read-only) password is refused - it cannot trade.

WHAT HAPPENS ON A TICKET
    opened   a market BUY (CE) or SELL (PE) of the instrument itself, at the ticket's own lots
             (Exness lots: 1 = 1 BTC / 100 oz of gold, 0.01 the smallest), with the ticket's STOP
             and its exit TARGET attached as Exness's own stop-loss and take-profit - so both rest
             at Exness and hold even while this server is down. Refused (and said so) when the
             account's free margin does not cover the order, or after MAX_ENTRIES_PER_DAY.
    trailed  the stop-loss at Exness is moved to the ticket's new stop (T1, the Supertrend trail).
    closed   the position is closed at Exness (if Exness's own stop or target has not already).
    Every POLL_S the open positions are read back; one Exness has closed by its stop-loss or
    take-profit closes the tool's ticket too, at Exness's own price.

The REST calls are MetaApi's documented ones (mt-client-api-v1.<region>.agiliumtrade.ai
/api-docs.json): POST .../trade {actionType ORDER_TYPE_BUY / ORDER_TYPE_SELL / POSITION_MODIFY /
POSITION_CLOSE_ID, symbol, volume, stopLoss, takeProfit, positionId, comment, clientId} ->
{numericCode, stringCode, message, orderId, positionId} (10009 TRADE_RETCODE_DONE); GET
.../positions, .../positions/{id}, .../history-deals/position/{id}, .../symbols,
.../symbols/{symbol}/specification, .../account-information.
"""
import copy
import datetime as dt
import json
import math
import os
import queue
import threading
import time

import requests

import config
import user_exness

INDICES = ("BTC", "GOLD")
POLL_S = 10
MAX_ENTRIES_PER_DAY = 20
DONE_CODES = (10008, 10009, 10010)     # placed / done / done partially
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_executors = {}
_lock = threading.Lock()


def for_account(email, log_path, close_ticket, client_factory=None, start=True):
    """One executor per account for the whole process (each feed hands it on)."""
    with _lock:
        ex = _executors.get(email)
        if ex is None:
            ex = _executors[email] = Executor(email, log_path, close_ticket=close_ticket,
                                              client_factory=client_factory, start=start)
        else:
            ex.close_ticket = close_ticket
        return ex


class ExnessOrderError(RuntimeError):
    pass


class Client:
    """One Exness account's MetaApi calls. The token only ever rides in the auth-token header."""

    def __init__(self, token, acct, session=None, timeout=20):
        self.token, self.acct, self.timeout = token, acct, timeout
        self.session = session or requests.Session()
        self.base = (f"https://mt-client-api-v1.{acct.get('region') or 'new-york'}.agiliumtrade.ai"
                     f"/users/current/accounts/{acct['id']}")
        self._symbols = None
        self._specs = {}

    def _req(self, method, path, body=None):
        r = self.session.request(method, self.base + path, json=body, timeout=self.timeout,
                                 headers={"auth-token": self.token, "Accept": "application/json"})
        if r.status_code == 404 and method == "GET" and "/positions/" in path:
            return None
        if r.status_code >= 400:
            try:
                body_ = r.json() or {}
                msg = body_.get("message") or ""
                # MetaApi names the field it refused (details: [{parameter, message}]) - say which.
                det = "; ".join(f"{d.get('parameter')}: {d.get('message')}" for d in (body_.get("details") or [])
                                if isinstance(d, dict))
                if det:
                    msg = f"{msg} - {det}"
            except ValueError:
                msg = ""
            raise ExnessOrderError(f"MetaApi HTTP {r.status_code}{': ' + msg[:160] if msg else ''}"
                                   .replace(self.token, "<token>"))
        return r.json() if r.content else None

    def symbol(self, base):
        if self._symbols is None:
            self._symbols = set(self._req("GET", "/symbols") or [])
        for suf in ("", "m", "c", "r"):
            if base + suf in self._symbols:
                return base + suf
        raise ExnessOrderError(f"{base} is not offered on this Exness account.")

    def spec(self, sym):
        if sym not in self._specs:
            self._specs[sym] = self._req("GET", f"/symbols/{sym}/specification") or {}
        return self._specs[sym]

    def trade(self, body):
        return self._req("POST", "/trade", body) or {}

    def position(self, pid):
        return self._req("GET", f"/positions/{pid}")

    def deals(self, pid):
        return self._req("GET", f"/history-deals/position/{pid}") or []

    def info(self):
        return self._req("GET", "/account-information") or {}

    def price(self, sym):
        return self._req("GET", f"/symbols/{sym}/current-price") or {}


def _client_factory(email):
    def make(acct):
        token = user_exness.token_for(email)
        if not token:
            raise ExnessOrderError("No Exness connection on this account - add it on the Exness page.")
        return Client(token, acct)
    return make


def _round_step(v, step):
    return math.floor(v / step + 1e-9) * step


class Executor:
    def __init__(self, email, log_path, close_ticket=None, client_factory=None, start=True, clock=None):
        self.email = email
        self.path = (log_path + ".exness_live.json") if log_path else None
        self.close_ticket = close_ticket
        self.make_client = client_factory or _client_factory(email)
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.enabled, self.enabled_ai = {k: False for k in INDICES}, {k: False for k in INDICES}
        self.acct_for = {"rule": {}, "ai": {}}           # source -> index -> account id
        self.positions, self.notes = {}, []
        self.day, self.entries_today = None, 0
        self._clients = {}
        self._q = queue.Queue()
        self._load()
        if start:
            threading.Thread(target=self._loop, daemon=True, name=f"exness-orders:{email}").start()

    # ---------------------------------------------------------------- state on disk
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            return
        self.enabled.update({k: bool(v) for k, v in (s.get("enabled") or {}).items() if k in INDICES})
        self.enabled_ai.update({k: bool(v) for k, v in (s.get("enabled_ai") or {}).items() if k in INDICES})
        self.acct_for = {"rule": dict((s.get("acct_for") or {}).get("rule") or {}),
                         "ai": dict((s.get("acct_for") or {}).get("ai") or {})}
        self.positions = dict(s.get("positions") or {})
        self.notes = list(s.get("notes") or [])[:30]
        self.day, self.entries_today = s.get("day"), int(s.get("entries_today") or 0)

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"enabled": self.enabled, "enabled_ai": self.enabled_ai, "acct_for": self.acct_for,
                       "positions": self.positions, "notes": self.notes[:30], "day": self.day,
                       "entries_today": self.entries_today}, fh)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _today(self):
        return dt.datetime.now(IST).strftime("%Y-%m-%d")

    def _note(self, index, text, level="info"):
        self.notes.insert(0, {"at": dt.datetime.now(IST).strftime("%H:%M:%S"), "index": index,
                              "text": text, "level": level})
        del self.notes[30:]

    # ---------------------------------------------------------------- the switches
    def switches(self, source="rule"):
        return self.enabled_ai if source == "ai" else self.enabled

    def set_enabled(self, index, on, source="rule", account_id=None):
        """On needs an account: one of this user's connected Exness accounts that can trade."""
        if index not in INDICES:
            raise ValueError(f"Live orders on Exness are for {' and '.join(INDICES)} only.")
        with self.lock:
            if on:
                acct = user_exness.account(self.email, account_id or self.acct_for[source].get(index))
                if acct is None:
                    raise ValueError("Choose one of your connected Exness accounts (the Exness page).")
                if acct.get("investor"):
                    raise ValueError("That account is connected with MetaApi's investor (read-only) password, "
                                     "so it cannot trade. Reconnect it in MetaApi with the trading password.")
                self.acct_for[source][index] = acct["id"]
            self.switches(source)[index] = bool(on)
            self._save()

    def account_for(self, index, source="rule"):
        aid = self.acct_for.get(source, {}).get(index)
        return user_exness.account(self.email, aid) if aid else None

    # ---------------------------------------------------------------- events from the ticket book
    def on_ticket_event(self, kind, trade, source="rule", **info):
        """Never blocks the ticket book: the work happens on this executor's own thread."""
        if (trade or {}).get("index") not in INDICES or kind not in ("opened", "trailed", "closed"):
            return
        self._q.put((kind, copy.deepcopy(trade), source))

    def _loop(self):
        last_poll = 0.0
        while True:
            try:
                kind, trade, source = self._q.get(timeout=1.0)
                self.handle(kind, trade, source)
            except queue.Empty:
                pass
            except Exception as exc:                       # one bad event must not stop the executor
                with self.lock:
                    self._note("", f"{type(exc).__name__}: {exc}", "error")
            if self.clock() - last_poll >= POLL_S:
                last_poll = self.clock()
                try:
                    self.poll()
                except Exception as exc:
                    with self.lock:
                        self._note("", f"checking positions: {type(exc).__name__}: {exc}", "error")

    def handle(self, kind, trade, source="rule"):
        with self.lock:
            if kind == "opened":
                self._enter(trade, source)
            elif kind == "trailed":
                self._trail(trade)
            elif kind == "closed":
                self._exit(trade)
            self._save()

    def _client(self, acct):
        c = self._clients.get(acct["id"])
        if c is None:
            c = self._clients[acct["id"]] = self.make_client(acct)
        return c

    # ---------------------------------------------------------------- entry
    def _enter(self, trade, source):
        index, tid = trade["index"], trade.get("trade_id")
        if not self.switches(source).get(index) or not tid or tid in self.positions:
            return
        acct = self.account_for(index, source)
        if acct is None:
            self._note(index, "Live orders are on but no Exness account is chosen - nothing sent.", "error")
            return
        if self.day != self._today():
            self.day, self.entries_today = self._today(), 0
        if self.entries_today >= MAX_ENTRIES_PER_DAY:
            self._note(index, f"{MAX_ENTRIES_PER_DAY} live entries today - the daily maximum; nothing sent.", "error")
            return
        side = 1 if trade.get("option_type") == "CE" else -1
        targets = trade.get("index_targets") or []
        exit_key = trade.get("exit_at") or "T2"
        k = {"T1": 0, "T2": 1, "T3": 2}.get(exit_key, 1)
        tp = targets[k] if len(targets) > k else None
        sl = trade.get("index_sl")
        pos = {"trade_id": tid, "index": index, "source": source, "account": acct["id"], "kind": acct.get("kind"),
               "side": "BUY" if side > 0 else "SELL", "state": "failed", "day": self._today(),
               "paper_entry": trade.get("entry_spot"), "sl": sl, "tp": tp, "exit_key": exit_key}
        self.positions[tid] = pos
        try:
            c = self._client(acct)
            base = (config.INSTRUMENTS.get(index) or {}).get("exness_symbol")
            sym = c.symbol(base)
            spec = c.spec(sym)
            step = float(spec.get("volumeStep") or 0.01)
            vmin, vmax = float(spec.get("minVolume") or 0.01), float(spec.get("maxVolume") or 1.0)
            vol = round(_round_step(min(float(trade.get("lots") or 0), vmax), step), 2)
            if vol < vmin:
                raise ExnessOrderError(f"{trade.get('lots')} lots is under Exness's minimum {vmin:g}.")
            digits = int(spec.get("digits") or 2)
            info = c.info()
            q = c.price(sym)
            px = float(q.get("ask") if side > 0 else q.get("bid"))
            lev = float(info.get("leverage") or 1) or 1.0
            need = px * float(spec.get("contractSize") or 1) * vol / lev
            free = float(info.get("freeMargin") or 0)
            pos.update(symbol=sym, qty=vol, margin_needed=round(need, 2))
            if need > free:
                raise ExnessOrderError(f"Not enough free margin: {vol:g} lot needs about ${need:,.2f} at 1:{lev:g}, "
                                       f"the account has ${free:,.2f} free.")
            # No clientId: MetaApi only accepts one shaped "<strategy>_<position>_<order>" (a plain
            # "TP..." tag was refused as "Validation failed" on the first real order, 4 Oct 2026),
            # and nothing here needs it - the position id MetaApi returns identifies the trade.
            body = {"actionType": "ORDER_TYPE_BUY" if side > 0 else "ORDER_TYPE_SELL", "symbol": sym, "volume": vol,
                    "comment": "TradePicker"}
            if sl is not None:
                body["stopLoss"] = round(float(sl), digits)
            if tp is not None:
                body["takeProfit"] = round(float(tp), digits)
            r = c.trade(body)
            if r.get("numericCode") not in DONE_CODES or not r.get("positionId"):
                raise ExnessOrderError(f"Exness refused: {r.get('stringCode') or r.get('numericCode')} "
                                       f"{r.get('message') or ''}".strip())
            pos.update(state="open", position_id=str(r["positionId"]), order_id=str(r.get("orderId") or ""))
            self.entries_today += 1
            p = c.position(pos["position_id"]) or {}
            if p.get("openPrice") is not None:
                pos.update(avg_price=float(p["openPrice"]), profit=p.get("profit"))
            self._note(index, f"{pos['side']} {vol:g} {sym} on your {acct.get('kind', '').upper()} Exness account"
                              + (f" at {pos.get('avg_price')}" if pos.get("avg_price") else "")
                              + (f", stop {body.get('stopLoss')} and target {body.get('takeProfit')} at Exness"
                                 if sl is not None or tp is not None else ""))
        except Exception as exc:
            pos.update(state="failed", error=str(exc)[:200])
            self._note(index, f"Live order NOT placed: {exc}", "error")

    # ---------------------------------------------------------------- the stop follows the ticket
    def _trail(self, trade):
        pos = self.positions.get(trade.get("trade_id"))
        sl = trade.get("index_sl")
        if not pos or pos.get("state") != "open" or sl is None or sl == pos.get("sl"):
            return
        acct = user_exness.account(self.email, pos["account"])
        if acct is None:
            return
        try:
            c = self._client(acct)
            digits = int(c.spec(pos["symbol"]).get("digits") or 2)
            body = {"actionType": "POSITION_MODIFY", "positionId": pos["position_id"], "stopLoss": round(float(sl), digits)}
            if pos.get("tp") is not None:
                body["takeProfit"] = round(float(pos["tp"]), digits)
            r = c.trade(body)
            if r.get("numericCode") not in DONE_CODES:
                raise ExnessOrderError(f"{r.get('stringCode') or r.get('numericCode')} {r.get('message') or ''}")
            pos["sl"] = sl
            self._note(pos["index"], f"Stop moved to {body['stopLoss']} at Exness.")
        except Exception as exc:
            self._note(pos["index"], f"Could not move the stop at Exness: {exc}", "error")

    # ---------------------------------------------------------------- exit
    def _exit(self, trade):
        pos = self.positions.get(trade.get("trade_id"))
        if not pos or pos.get("state") != "open":
            return
        acct = user_exness.account(self.email, pos["account"])
        if acct is None:
            self._note(pos["index"], "The Exness account was removed - close the position on Exness yourself.", "error")
            pos["state"] = "attention"
            return
        try:
            c = self._client(acct)
            if c.position(pos["position_id"]) is not None:
                r = c.trade({"actionType": "POSITION_CLOSE_ID", "positionId": pos["position_id"]})
                if r.get("numericCode") not in DONE_CODES:
                    raise ExnessOrderError(f"{r.get('stringCode') or r.get('numericCode')} {r.get('message') or ''}")
            self._bank(c, pos, "closed with the ticket")
        except Exception as exc:
            pos["state"] = "attention"
            self._note(pos["index"], f"Could not close at Exness - CHECK EXNESS NOW: {exc}", "error")

    def _bank(self, c, pos, why):
        """Read Exness's own closing deal: price and profit."""
        pos["state"] = "closed"
        pos["exit_reason"] = why
        try:
            deals = [d for d in c.deals(pos["position_id"]) if str(d.get("entryType", "")).endswith("OUT")]
        except Exception:
            deals = []
        if deals:
            d = deals[-1]
            pos["exit_price"] = d.get("price")
            pos["gross_pnl"] = round(sum(float(x.get("profit") or 0) for x in deals), 2)
            pos["swap"] = round(sum(float(x.get("swap") or 0) for x in deals), 2)
            pos["deal_reason"] = d.get("reason")
        self._note(pos["index"], f"{pos['side']} {pos.get('qty')} {pos.get('symbol')} closed at Exness"
                                 + (f" at {pos.get('exit_price')}" if pos.get("exit_price") else "")
                                 + (f", result ${pos.get('gross_pnl')}" if pos.get("gross_pnl") is not None else "")
                                 + f" ({why}).")

    # ---------------------------------------------------------------- Exness closed it first
    def poll(self):
        with self.lock:
            open_ = [p for p in self.positions.values() if p.get("state") == "open"]
        for pos in open_:
            acct = user_exness.account(self.email, pos["account"])
            if acct is None:
                continue
            c = self._client(acct)
            p = c.position(pos["position_id"])
            with self.lock:
                if p is not None:
                    pos.update(profit=p.get("profit"), mark=p.get("currentPrice"), swap=p.get("swap"))
                    continue
                self._bank(c, pos, "Exness's own stop-loss or take-profit")
                reason = str(pos.get("deal_reason") or "")
                status = ("CLOSED — stop-loss hit (filled at Exness)" if reason.endswith("SL")
                          else f"CLOSED — {pos.get('exit_key') or 'T2'} hit (full target reached, filled at Exness)"
                          if reason.endswith("TP")
                          else "CLOSED — closed at Exness")
                self._save()
            if self.close_ticket is not None:
                try:
                    self.close_ticket(pos["index"], status, pos.get("exit_price"), trade_id=pos["trade_id"])
                except Exception:
                    pass

    # ---------------------------------------------------------------- what pages and the AI desk read
    def real_entry(self, trade_id):
        p = self.positions.get(trade_id)
        if p and p.get("avg_price") is not None and p.get("state") in ("open", "closed"):
            return float(p["avg_price"]), "Exness"
        return None

    def real_pnl(self, trade_id):
        p = self.positions.get(trade_id)
        return None if not p or p.get("profit") is None else float(p["profit"])

    def funds_check(self, need, source="ai", index="BTC"):
        acct = self.account_for(index, source)
        if acct is None:
            return {"enough": None, "note": "no Exness account chosen"}
        try:
            free = float(self._client(acct).info().get("freeMargin") or 0)
        except Exception as exc:
            return {"enough": None, "note": f"could not read the account: {type(exc).__name__}"}
        return {"enough": free >= float(need or 0), "shortfall": round(max(0.0, float(need or 0) - free), 2)}

    def fills(self, source=None):
        return [{"trade_id": p["trade_id"], "entry_avg": p.get("avg_price"), "exit_avg": p.get("exit_price"),
                 "paper_entry": p.get("paper_entry"), "gross_pnl": p.get("gross_pnl"), "qty": p.get("qty")}
                for p in self.positions.values()
                if p.get("state") == "closed" and (source is None or p.get("source") == source)]

    def public(self):
        with self.lock:
            today = self._today()
            pos = [{k: p.get(k) for k in ("trade_id", "index", "state", "source", "symbol", "qty", "side", "kind",
                                          "avg_price", "sl", "tp", "mark", "profit", "swap", "exit_price",
                                          "gross_pnl", "exit_reason", "error", "margin_needed")}
                   for p in self.positions.values() if p.get("day") == today or p.get("state") in ("open", "attention")]
            for p in pos:
                p["tradingsymbol"] = p.get("symbol")
                p["filled_qty"] = p.get("qty")
                p["stop_trigger"] = p.get("sl")
                p["stop_at_venue"] = p.get("state") == "open"
            pos.sort(key=lambda p: p.get("trade_id") or "", reverse=True)
            label = {}
            for src in ("rule", "ai"):
                for k in INDICES:
                    a = self.account_for(k, src)
                    if a:
                        label.setdefault(k, {})[src] = {"id": a["id"], "kind": a.get("kind"), "server": a.get("server")}
            return {"enabled": dict(self.enabled), "enabled_ai": dict(self.enabled_ai), "venue": "Exness",
                    "accounts": label, "positions": pos, "notes": self.notes[:8],
                    "entries_today": self.entries_today if self.day == today else 0, "max_entries": MAX_ENTRIES_PER_DAY}
