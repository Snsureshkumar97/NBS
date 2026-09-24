#!/usr/bin/env python3
"""
delta_testnet_stop_check.py - can Delta Exchange India hold a stop order on a BTC option?
================================================================================
Asked for by the user on 24 Sep 2026. delta_orders.py's stop lives in THIS tool
(it reads Delta's mark every second and sells) because the tool was written on
the belief that "Delta does not accept stop orders on options". No source for
that belief could be found, and Delta's API docs do not say it. If a resting
reduce-only stop works on a held option, the tool's one real weakness - a stop
that is only watched while the server is up - goes away. This settles it with
Delta's own answers, on Delta's TESTNET, with play money.

WHAT IT CAN AND CANNOT TOUCH
    * ONE host: Delta India's testnet (user_delta.TESTNET). Every request is
      checked against it first, and there is no option to point it anywhere
      else. It has no way to reach your real account.
    * Only the keys in the environment - DELTA_TESTNET_KEY and
      DELTA_TESTNET_SECRET, made on a Demo account at testnet.delta.exchange
      (Delta: production keys work only on production, Demo keys only on the
      testnet). It never reads this tool's own saved keys, and never prints,
      logs or stores either value.
    * Its own orders only: every order carries a TPCHK client id, everything it
      places is cancelled at the end (also when it crashes), and it sells back
      no more than it bought itself.

RUN IT
    export DELTA_TESTNET_KEY=...       # from the Demo account, trading permission,
    export DELTA_TESTNET_SECRET=...    # this computer's IP whitelisted on the key
    python3 delta_testnet_stop_check.py                   # stage 1
    python3 delta_testnet_stop_check.py --with-position   # stages 1 and 2

    Stage 1 holds no position and nothing can fill (its buy sits far below the
    market): it only asks whether Delta ACCEPTS stop orders on the option, and
    on a perpetual as a control that the request itself is well formed.
    Stage 2 (--with-position) buys ONE contract of the option on the testnet,
    asks for a reduce-only stop on it, reads that order back to see whether it
    rests at Delta, then cancels it and sells the contract. This is the answer
    that matters; stage 1 alone can be inconclusive (a stop with nothing to
    reduce can be refused for that reason, not because of the product).

A full transcript (no keys) is written to ~/delta-testnet-check-<time>.json,
outside this repository, to send to Delta with the question.
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.parse

import user_delta

TESTNET_HOST = urllib.parse.urlparse(user_delta.TESTNET).hostname
PREFIX = "TPCHK"
FILL_WAIT_S = 20


class NotTestnet(Exception):
    """Something tried to reach a host that is not Delta's testnet."""


def guard(base):
    """Refuse anything but the testnet host. Called on construction and again on
    every request, so a changed base cannot slip past."""
    host = urllib.parse.urlparse(base).hostname or ""
    if host != TESTNET_HOST or "testnet" not in host:
        raise NotTestnet(f"{base!r} is not Delta's testnet ({TESTNET_HOST}) - this check will not touch it.")


def _floor(x, tick):
    return math.floor(x / tick + 1e-9) * tick


def _ceil(x, tick):
    return math.ceil(x / tick - 1e-9) * tick


def _price(x, tick):
    """A price as Delta takes it: a string at the tick's own precision."""
    places = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return f"{x:.{places}f}"


# =============================================================================
class Reply:
    """One Delta answer: ok, or Delta's own error code and context."""

    def __init__(self, http, body):
        self.http = http
        self.body = body if isinstance(body, dict) else {}
        err = self.body.get("error") or {}
        self.ok = http == 200 and self.body.get("success", True) is not False
        self.code = None if self.ok else (err.get("code") or f"http {http}")
        self.context = {} if self.ok else (err.get("context") or {})
        self.result = self.body.get("result") if self.ok else None


class Api:
    """Signed calls to the testnet, with everything asked and answered kept."""

    def __init__(self, send, base=user_delta.TESTNET):
        guard(base)
        self.send, self.base, self.log = send, base, []

    def call(self, method, path, params=None, body=None):
        guard(self.base)
        rep = Reply(*self.send(method, path, params, body))
        self.log.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         "method": method, "path": path, "params": params, "body": body,
                         "http": rep.http, "ok": rep.ok, "code": rep.code, "context": rep.context,
                         "result": rep.result if isinstance(rep.result, (dict, list)) and len(json.dumps(rep.result)) < 4000
                         else ("(long)" if rep.result else rep.result)})
        return rep


def requests_transport(key, secret, base=user_delta.TESTNET):
    """The real transport: the tool's own signing (user_delta.headers), one host."""
    import requests

    def send(method, path, params=None, body=None):
        guard(base)
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        data = json.dumps(body, separators=(",", ":")) if body is not None else ""
        h = user_delta.headers(key, secret, method.upper(), path, query, data)
        r = requests.request(method.upper(), base + path + query, headers=h, data=data or None, timeout=15)
        try:
            out = r.json()
        except ValueError:
            out = {}
        return r.status_code, out
    return send


# =============================================================================
def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def market(api, symbol):
    """{"symbol", "id", "tick", "mark", "bid", "ask"} or None."""
    p, t = api.call("GET", f"/v2/products/{symbol}"), api.call("GET", f"/v2/tickers/{symbol}")
    if not (p.ok and t.ok and isinstance(p.result, dict) and isinstance(t.result, dict)):
        return None
    q = t.result.get("quotes") or {}
    return {"symbol": symbol, "id": int(p.result["id"]), "tick": _f(p.result.get("tick_size")) or 0.1,
            "mark": _f(t.result.get("mark_price")), "bid": _f(q.get("best_bid")), "ask": _f(q.get("best_ask")),
            "settlement": p.result.get("settlement_time")}


def pick_option(api, need_book, symbol=None):
    """A live BTC (else ETH) call option with a real price. One with a two-sided
    book always beats one without, and the narrowest spread wins; a caller that
    will trade it (need_book) is not offered one without a book at all."""
    if symbol:
        m = market(api, symbol)
        return m if m and m["mark"] else None
    for asset in ("BTC", "ETH"):
        rep = api.call("GET", "/v2/tickers", {"contract_types": "call_options", "underlying_asset_symbols": asset})
        rows = [r for r in (rep.result or []) if isinstance(r, dict)] if rep.ok else []
        good = []
        for r in rows:
            mark, q = _f(r.get("mark_price")), r.get("quotes") or {}
            bid, ask = _f(q.get("best_bid")), _f(q.get("best_ask"))
            if not mark or mark <= 0:
                continue
            if need_book and not (bid and ask and ask > 0):
                continue
            has_book = bool(bid and ask and ask > 0)
            good.append((0 if has_book else 1, abs(ask - bid) / mark if has_book else 0.0, str(r.get("symbol"))))
        for _b, _sp, sym in sorted(good):
            m = market(api, sym)
            if m and m["mark"] and m["mark"] >= 20 * m["tick"]:
                return m
    return None


def coid(n):
    return f"{PREFIX}{int(time.time()) % 10**8}{n}"


class Check:
    def __init__(self, api, out=print, sleep=time.sleep, clock=time.time):
        self.api, self.out, self.sleep, self.clock = api, out, sleep, clock
        self.attempts = []
        self.n = 0
        self.bought = {}            # product id -> contracts this run bought
        self.touched = set()        # product ids this run placed anything on

    # ---- one order, and what Delta said
    def place(self, name, m, **body):
        self.n += 1
        body = dict(body, product_id=m["id"], time_in_force="gtc", client_order_id=coid(self.n))
        self.touched.add(m["id"])
        rep = self.api.call("POST", "/v2/orders", body=body)
        a = {"name": name, "symbol": m["symbol"], "accepted": rep.ok, "code": rep.code, "context": rep.context,
             "order_id": str(rep.result["id"]) if rep.ok and isinstance(rep.result, dict) and rep.result.get("id") else None,
             "sent": {k: v for k, v in body.items() if k not in ("product_id", "time_in_force", "client_order_id")}}
        self.attempts.append(a)
        self.out(f"  {'ACCEPTED' if a['accepted'] else 'REFUSED '}  {name}"
                 + ("" if a["accepted"] else f"  ->  {a['code']} {json.dumps(a['context']) if a['context'] else ''}"))
        return rep, a

    def cancel(self, a, m):
        if a.get("order_id"):
            self.api.call("DELETE", "/v2/orders", body={"id": int(a["order_id"]), "product_id": m["id"]})

    def read_back(self, a):
        rep = self.api.call("GET", f"/v2/orders/{int(a['order_id'])}")
        if rep.ok and isinstance(rep.result, dict):
            r = rep.result
            a["read_back"] = {k: r.get(k) for k in ("state", "stop_order_type", "stop_price", "reduce_only",
                                                    "size", "order_type", "stop_trigger_method")}
            self.out(f"            Delta shows it as: {a['read_back']}")

    # ---- stage 1
    def stage1(self, opt, perp):
        tick, mark = opt["tick"], opt["mark"]
        stop = _floor(mark * 0.5, tick)
        self.out(f"\nSTAGE 1 - does Delta ACCEPT stop orders on {opt['symbol']} (mark {mark})? No position; nothing can fill.")
        base_ok = False
        for frac in (0.5, 0.8):
            rep, a = self.place(f"baseline: plain buy limit far below the market ({frac:.0%} of mark), post-only", opt,
                                size=1, side="buy", order_type="limit_order", post_only=True,
                                limit_price=_price(_floor(mark * frac, tick), tick))
            if a["accepted"]:
                base_ok = True
                self.cancel(a, opt)
                break
        self.baseline_ok = base_ok
        shape = dict(size=1, side="sell", stop_order_type="stop_loss_order", stop_trigger_method="mark_price")
        for label, extra in (("stop-market sell (no reduce-only)", dict(order_type="market_order", stop_price=_price(stop, tick))),
                             ("stop-market sell, REDUCE-ONLY", dict(order_type="market_order", stop_price=_price(stop, tick), reduce_only=True)),
                             ("stop-limit sell, REDUCE-ONLY", dict(order_type="limit_order", stop_price=_price(stop, tick),
                                                                   limit_price=_price(_floor(stop * 0.95, tick), tick), reduce_only=True))):
            rep, a = self.place(f"option: {label}", opt, **shape, **extra)
            if a["accepted"]:
                self.cancel(a, opt)
        if perp:
            ptick = perp["tick"]
            rep, a = self.place("CONTROL on the BTCUSD perpetual: stop-market sell (no reduce-only), far below",
                                perp, size=1, side="sell", order_type="market_order", stop_order_type="stop_loss_order",
                                stop_trigger_method="mark_price", stop_price=_price(_floor(perp["mark"] * 0.4, ptick), ptick))
            if a["accepted"]:
                self.cancel(a, perp)
        else:
            self.out("  (no BTCUSD perpetual found on the testnet - no control)")

    # ---- stage 2
    def stage2(self, opt):
        tick = opt["tick"]
        self.out(f"\nSTAGE 2 - holding ONE contract of {opt['symbol']} on the testnet, does Delta take a reduce-only stop on it?")
        rep, buy = self.place("buy 1 contract (limit a little above the ask)", opt, size=1, side="buy", order_type="limit_order",
                              limit_price=_price(_ceil(opt["ask"] * 1.02, tick), tick))
        if not buy["accepted"]:
            self.out("  The buy was refused, so there is nothing to protect - stage 2 cannot run.")
            return
        filled = 0
        waited = 0
        while waited < FILL_WAIT_S:
            o = self.api.call("GET", f"/v2/orders/{int(buy['order_id'])}")
            r = o.result if o.ok and isinstance(o.result, dict) else {}
            filled = max(0, int(r.get("size") or 0) - int(r.get("unfilled_size") or 0))
            if filled or r.get("state") in ("closed", "cancelled"):
                break
            self.sleep(1)
            waited += 1
        if not filled:
            self.cancel(buy, opt)
            self.out(f"  The buy did not fill in {FILL_WAIT_S}s (thin testnet book) - cancelled; nothing held, stage 2 cannot run.")
            return
        self.bought[opt["id"]] = self.bought.get(opt["id"], 0) + filled
        self.out(f"  Bought {filled}.")
        mark = market(self.api, opt["symbol"])
        mark = (mark or opt)["mark"] or opt["mark"]
        stop = _floor(mark * 0.5, tick)
        shape = dict(size=filled, side="sell", stop_order_type="stop_loss_order", stop_trigger_method="mark_price", reduce_only=True)
        for label, extra in (("HELD position: reduce-only stop-market sell", dict(order_type="market_order", stop_price=_price(stop, tick))),
                             ("HELD position: reduce-only stop-limit sell", dict(order_type="limit_order", stop_price=_price(stop, tick),
                                                                                limit_price=_price(_floor(stop * 0.95, tick), tick)))):
            rep, a = self.place(f"option: {label}", opt, **shape, **extra)
            if a["accepted"]:
                self.read_back(a)
                self.cancel(a, opt)

    # ---- always
    def cleanup(self, products):
        """Cancel what this run left resting; sell back what it bought and no more."""
        for m in products:
            if m["id"] not in self.touched:
                continue
            o = self.api.call("GET", "/v2/orders", {"product_ids": str(m["id"]), "states": "open,pending"})
            for r in (o.result or []) if o.ok and isinstance(o.result, list) else []:
                if str(r.get("client_order_id") or "").startswith(PREFIX):
                    self.api.call("DELETE", "/v2/orders", body={"id": int(r["id"]), "product_id": m["id"]})
            mine = self.bought.get(m["id"], 0)
            if mine > 0:
                p = self.api.call("GET", "/v2/positions", {"product_id": str(m["id"])})
                rows = p.result if p.ok and isinstance(p.result, list) else [p.result] if p.ok and p.result else []
                net = sum(int(r.get("size") or 0) for r in rows if isinstance(r, dict))
                sell = min(net, mine)
                if sell > 0:
                    self.n += 1
                    self.api.call("POST", "/v2/orders", body={"product_id": m["id"], "size": sell, "side": "sell", "order_type": "market_order",
                                                              "reduce_only": True, "client_order_id": coid(self.n)})
                    self.out(f"  Sold back the {sell} contract(s) this run bought on {m['symbol']}.")


def verdict(attempts, stage2_ran, baseline_ok):
    """The plain answer, from Delta's own replies."""
    held = [a for a in attempts if a["name"].startswith("option: HELD")]
    opt = [a for a in attempts if a["name"].startswith("option:") and a not in held]
    ctl = [a for a in attempts if a["name"].startswith("CONTROL")]
    lines = []
    if held:
        yes = [a for a in held if a["accepted"]]
        if yes:
            lines.append("YES - Delta accepted a reduce-only stop on a HELD option position. The tool rests its stop at the "
                         "exchange (delta_orders.py does since 24 Sep 2026); the raw replies here show its exact shapes.")
            rb = [a.get("read_back") for a in yes if a.get("read_back")]
            if rb:
                lines.append(f"  It stayed open at Delta as: {rb[0]}")
            for a in held:
                if not a["accepted"]:
                    lines.append(f"  (but refused: {a['name']} -> {a['code']} {a['context'] or ''})")
        else:
            lines.append("NO - Delta refused a reduce-only stop on a HELD option position:")
            for a in held:
                lines.append(f"  {a['name']} -> {a['code']} {a['context'] or ''}")
            lines.append("  Then the stop order the tool sends after each fill will be refused too, and the tool's own watch of the mark is the only stop.")
        return "\n".join(lines)
    if not baseline_ok:
        lines.append("INCONCLUSIVE - even the plain far-away buy was refused, so the account, the key permission or the product "
                     "cannot take orders at all. Fix that first (see the codes above).")
    elif any(a["accepted"] for a in opt):
        lines.append("PROMISING - Delta accepted a stop order on the option without a position:")
        for a in opt:
            lines.append(f"  {'accepted' if a['accepted'] else 'refused '}  {a['name']}" + ("" if a["accepted"] else f" -> {a['code']}"))
        lines.append("  Run again with --with-position to confirm it with a real holding.")
    else:
        lines.append("NOT YET DECIDED - every stop on the option was refused with no position held:")
        for a in opt:
            lines.append(f"  {a['name']} -> {a['code']} {a['context'] or ''}")
        if ctl and ctl[0]["accepted"]:
            lines.append("  The same kind of request was ACCEPTED on the perpetual, so it is well formed: the refusal is about the "
                         "option or about holding nothing to reduce. Only --with-position can tell those two apart.")
        elif ctl:
            lines.append(f"  The control was refused too ({ctl[0]['code']}), so this may be the request or the account, not the option.")
    return "\n".join(lines)


def run(api, with_position=False, symbol=None, out=print):
    """The whole check. Returns {"attempts", "verdict", "log"}."""
    ck = Check(api, out=out)
    products = []
    ck.baseline_ok = False
    try:
        who = api.call("GET", "/v2/wallet/balances")
        if not who.ok:
            hint = {"ip_not_whitelisted_for_api_key": "this computer's IP is not whitelisted on the key"
                    + (f" (Delta saw {who.context.get('client_ip')})" if who.context.get("client_ip") else ""),
                    "invalid_api_key": "Delta does not know that key - is it from a Demo account, made at testnet.delta.exchange?",
                    "invalid_signature": "the secret does not match that key",
                    "unauthorized": "the key has no trading permission"}.get(who.code, "see the code")
            out(f"\nCould not sign in to the testnet: {who.code} - {hint}.")
            return {"attempts": [], "verdict": "NO ANSWER - could not sign in to the testnet.", "log": api.log}
        out("Signed in to Delta's testnet.")
        opt = pick_option(api, need_book=with_position, symbol=symbol)
        if not opt:
            out("\nThe testnet lists no BTC or ETH call option with a live price"
                + (" and a two-sided book" if with_position else "") + " right now. Try later, or pass --symbol.")
            return {"attempts": [], "verdict": "NO ANSWER - no usable option on the testnet.", "log": api.log}
        perp = market(api, "BTCUSD")
        products = [opt] + ([perp] if perp else [])
        ck.stage1(opt, perp)
        if with_position:
            ck.stage2(opt)
    finally:
        try:
            ck.cleanup(products)
        except Exception as exc:                       # never let cleanup hide the first error
            out(f"  CLEANUP FAILED ({exc}) - look at the testnet account's open orders and positions by hand.")
    v = verdict(ck.attempts, with_position, ck.baseline_ok)
    out("\n" + "=" * 78 + "\n" + v + "\n" + "=" * 78)
    return {"attempts": ck.attempts, "verdict": v, "log": api.log}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Does Delta India accept a stop order on a BTC option? (testnet only)")
    ap.add_argument("--with-position", action="store_true", help="stage 2: buy ONE contract on the testnet and test a reduce-only stop on it")
    ap.add_argument("--symbol", help="a specific testnet option, e.g. C-BTC-90000-250926 (default: one is picked)")
    args = ap.parse_args(argv)
    key, secret = os.environ.get("DELTA_TESTNET_KEY", "").strip(), os.environ.get("DELTA_TESTNET_SECRET", "").strip()
    if not key or not secret:
        print("Set DELTA_TESTNET_KEY and DELTA_TESTNET_SECRET (a Demo account's keys, from testnet.delta.exchange) first.\n"
              "This script never reads this tool's own Delta keys.")
        return 2
    api = Api(requests_transport(key, secret))
    res = run(api, with_position=args.with_position, symbol=args.symbol)
    path = os.path.expanduser("~/delta-testnet-check-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as fh:
        json.dump({"verdict": res["verdict"], "attempts": res["attempts"], "requests": res["log"]}, fh, indent=1, default=str)
    print(f"\nTranscript (no keys): {path}\nSend that file to Delta support with the question in delta_stop_order_question.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
