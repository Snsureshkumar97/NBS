"""
watchlist.py — option contracts you want to keep an eye on
===========================================================
Per account and per market. The list is stored beside that account's own trade
log, in its private per-account folder, so it is the same list on a phone and a
laptop, and an Indian contract never turns up in a crypto list. Prices only: a
watchlist never places anything.
"""
import datetime as dt
import json
import os
import re
import threading

import config
import trade_log

MAX_ITEMS = 50
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_lock = threading.Lock()
_DERIBIT_TAG = re.compile(r"^\d{1,2}[A-Z]{3}\d{2}$")


def _path(email, market):
    return os.path.join(os.path.dirname(trade_log.user_log_path(email, market)), "watchlist.json")


def strike_text(strike):
    """23100 -> "23100", 23100.5 -> "23100.5": the same text the page builds its ids from."""
    f = float(strike)
    return str(int(f)) if f.is_integer() else repr(f)


def item_id(index, expiry, strike, side):
    return f"{index}|{expiry}|{strike_text(strike)}|{side}"


def load(email, market):
    try:
        with open(_path(email, market)) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [x for x in (data.get("items") or []) if isinstance(x, dict) and x.get("id")]


def _save(email, market, items):
    path = _path(email, market)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"items": items}, fh, indent=1)
    os.replace(tmp, path)          # never a half-written list


def _clean(market, index, expiry, strike, side):
    index = (index or "").strip().upper()
    meta = config.INSTRUMENTS.get(index)
    if not meta or meta.get("market", config.DEFAULT_MARKET) != market:
        raise ValueError("That index is not traded in this market.")
    side = (side or "").strip().upper()
    if side not in ("CE", "PE"):
        raise ValueError("Pick a call (CE) or a put (PE).")
    try:
        strike = float(strike)
    except (TypeError, ValueError):
        raise ValueError("That strike is not a number.")
    if not strike > 0 or strike != strike:
        raise ValueError("That strike is not a number.")
    expiry = (expiry or "").strip()
    if not expiry or len(expiry) > 20 or not all(c.isalnum() or c == "-" for c in expiry):
        raise ValueError("That expiry is not one the chain lists.")
    return index, expiry, strike, side


def add(email, market, index, expiry, strike, side, price=None):
    """(id, added). Adding a contract already on the list is not an error."""
    index, expiry, strike, side = _clean(market, index, expiry, strike, side)
    iid = item_id(index, expiry, strike, side)
    try:
        price = round(float(price), 4)
        if price != price or price <= 0:
            price = None
    except (TypeError, ValueError):
        price = None
    with _lock:
        items = load(email, market)
        if any(x["id"] == iid for x in items):
            return iid, False
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"The watchlist holds {MAX_ITEMS} contracts - remove one first.")
        items.append({"id": iid, "index": index, "expiry": expiry, "strike": strike, "side": side,
                      "added": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"), "added_price": price})
        _save(email, market, items)
    return iid, True


def remove(email, market, iid):
    with _lock:
        items = load(email, market)
        left = [x for x in items if x.get("id") != iid]
        if len(left) == len(items):
            return False
        _save(email, market, left)
    return True


def _row(last, bid, ask, oi=None):
    num = lambda v: None if v in (None, 0) else round(float(v), 2)
    last, bid, ask = num(last), num(bid), num(ask)
    spread = None
    if bid and ask and ask >= bid:
        spread = round((ask - bid) / ((ask + bid) / 2) * 100, 2)
    return {"last": last, "bid": bid, "ask": ask, "spread_pct": spread, "oi": oi}


def quotes(provider, items, market):
    """{id: {last, bid, ask, spread_pct, oi} or {error}} for every item.

    The expiry must match exactly. The chain's own "nearest expiry if that one is
    gone" fallback is right for a signal and wrong here: an expired contract would
    quietly show the next week's price under last week's name.
    """
    out = {}
    if not items or provider is None:
        return out
    gone = {"error": "Not listed any more - expired?"}
    if config.MARKETS.get(market, {}).get("market_provider") == "kite":
        import data_providers as dp
        toks = {}
        for idx in {x["index"] for x in items}:
            try:
                _exch, opts = provider._all_option_instruments(idx)
            except Exception:
                opts = []
            for x in (i for i in items if i["index"] == idx):
                hit = next((o for o in opts
                            if str(o.get("expiry")) == x["expiry"]
                            and float(o.get("strike") or 0) == float(x["strike"])
                            and o.get("instrument_type") == x["side"]), None)
                if hit and hit.get("instrument_token"):
                    toks[str(int(hit["instrument_token"]))] = x["id"]
                else:
                    out[x["id"]] = dict(gone)
        tok_list = list(toks)
        for i in range(0, len(tok_list), 400):          # Kite allows 500 a call
            batch = tok_list[i:i + 400]
            try:
                q = provider.kite.quote([int(t) for t in batch])
            except Exception as exc:
                for t in batch:
                    out[toks[t]] = {"error": f"Zerodha did not answer: {type(exc).__name__}"}
                continue
            for t in batch:
                v = q.get(t) or q.get(int(t)) or {}
                bid, ask = dp._best_bid_ask(v.get("depth"))
                out[toks[t]] = _row(v.get("last_price"), bid, ask, v.get("oi"))
        return out

    # Deribit: one summary call per coin carries every option's mark, bid and ask,
    # quoted in the coin, so each is converted at the coin's index price.
    import data_providers as dp
    for idx in {x["index"] for x in items}:
        try:
            rows, px = provider._chain_rows(idx), float(provider.spot(idx))
        except Exception:
            rows, px = [], None
        for x in (i for i in items if i["index"] == idx):
            exp = x["expiry"].upper()
            tag = exp if _DERIBIT_TAG.match(exp) else dp._deribit_tag(x["expiry"])
            kind = "C" if x["side"] == "CE" else "P"
            r = next((r for r in rows if r["expiry"] == tag and r["strike"] == int(float(x["strike"]))
                      and r["kind"] == kind), None)
            if r is None or px is None:
                out[x["id"]] = dict(gone)
                continue
            usd = lambda c: None if c is None else c * px
            out[x["id"]] = _row(usd(r.get("mark_coin")), usd(r.get("bid_coin")), usd(r.get("ask_coin")), r.get("oi"))
    return out
