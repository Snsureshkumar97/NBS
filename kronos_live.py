"""
kronos_live.py — the server's side of the Kronos tab: candles out to the worker, its forecasts and record back in
================================================================================
The user, 12 Oct 2026: "can you add a tab for kronos i want to see on the real data how it works" - after
kronos_study.py found Kronos (an open candlestick foundation model) calls the next hour's direction no better than a
coin on the Indian indices, and as a filter costs most of the profit. So this is RESEARCH ONLY: nothing here reaches
a signal, a ticket or an order.

Kronos needs PyTorch, which the trading server never loads. It runs in its own process, kronos_worker.py, in its own
environment (~/kronos on the server), at the lowest priority. The two meet only through files under
<log dir>/kronos/:
    in/<INDEX>.json    written here: the index's latest CLOSED 15-minute candles and the tool's direction then
    out/<INDEX>.json   written by the worker: its forecast of the next four candles from the last one
    calls/<INDEX>.json written by the worker: every call it made, scored once the hour has passed
    worker.json        written by the worker: its heartbeat
If the worker stops, the tab says so and nothing else notices.
"""
import datetime as dt
import json
import os
import threading
import time

import config
import trade_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STEP = dt.timedelta(minutes=15)
KEEP_CANDLES = 600            # what the worker gets: 400 of context and room to replay the last two sessions
CHART_CANDLES = 80            # what the tab draws before the forecast - two sessions and a half
CALLS_SHOWN = 30              # the newest calls listed per index on the tab
MARKET = "nse_index"
# Bitcoin and gold too (the user, 12 Oct 2026: "can you add it for crypto as well"). On Exness they are CFDs - no
# option chain, no strike: Kronos's trade is the instrument itself, bought or sold, in its own price.
MARKETS = ("nse_index", "crypto")
WORKER_STALE_S = 180          # the worker beats every ~20 s; three minutes without one is "not running"

_lock = threading.Lock()
_exported = {}                # index -> the last candle time written, so a file is rewritten once per candle
_thread = None


def base():
    return os.path.join(trade_log.log_dir(), "kronos")


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def closed_candles(df, now):
    """[[iso time, open, high, low, close], ...] of the candles that had CLOSED by `now` - a 15-minute candle that
    started at t closes at t + 15 minutes. The forming candle is never sent: the worker forecasts from what is known."""
    out = []
    if df is None or not len(df):
        return out
    for t, o, h, l, c in zip(df.index, df["Open"], df["High"], df["Low"], df["Close"]):
        ts = t.to_pydatetime() if hasattr(t, "to_pydatetime") else t
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if ts + STEP > now:
            continue
        vals = [float(o), float(h), float(l), float(c)]
        if any(v != v for v in vals):
            continue
        out.append([ts.astimezone(IST).isoformat()] + vals)
    return out[-KEEP_CANDLES:]


def tool_direction(rec):
    """The tool's own signal at that moment: +1 bullish, -1 bearish, 0 no direction."""
    bias = str((rec or {}).get("bias") or "").upper()
    return 1 if bias.startswith("BULL") else -1 if bias.startswith("BEAR") else 0


def chain_prices(feed, key, close, step):
    """The live call and put prices of the strikes within two steps of the price, from the feed's own option chain
    (in memory - no broker request): {"expiry", "strikes": {"22350": {"CE": 118.4, "PE": 104.1}, ...}}, or None. The
    streamed price when there is one, else the chain snapshot's."""
    try:
        oi = feed.live_chain(key)
    except Exception:
        oi = None
    if not isinstance(oi, dict) or not oi.get("strikes") or not step:
        return None
    try:
        live = feed.chain_live(key) or {}
    except Exception:
        live = {}
    atm = round(close / step) * step
    out = {}
    for s_ in oi["strikes"]:
        k = s_.get("strike")
        if k is None or abs(float(k) - atm) > 2 * step:
            continue
        ce = (live.get((float(k), "CE")) or {}).get("ltp") or s_.get("call_ltp")
        pe = (live.get((float(k), "PE")) or {}).get("ltp") or s_.get("put_ltp")
        out[str(int(round(float(k))))] = {"CE": ce, "PE": pe}
    return {"expiry": oi.get("expiry"), "strikes": out} if out else None


def _is_cfd(market):
    return market == "crypto" and getattr(config, "CRYPTO_VENUE", "") == "exness"


def export(feed, now=None, market=MARKET):
    """Write in/<INSTRUMENT>.json for each instrument of `market` whose latest closed candle is newer than the last one
    written. Returns the instruments written."""
    now = now or dt.datetime.now(IST)
    wrote = []
    cfd = _is_cfd(market)
    for key in config.instruments_in(market):
        try:
            df, rec = feed.candles(key)
        except Exception:
            continue
        rows = closed_candles(df, now)
        if not rows:
            continue
        last = rows[-1][0]
        with _lock:
            if _exported.get(key) == last:
                continue
            _exported[key] = last
        meta = config.INSTRUMENTS.get(key) or {}
        step = meta.get("strike_step")
        _write_json(os.path.join(base(), "in", f"{key}.json"),
                    {"index": key, "market": market, "cfd": cfd, "candles": rows,
                     "tool": {"dir": tool_direction(rec), "bias": (rec or {}).get("bias")},
                     # what Kronos's own trade is priced with: the strikes' live prices, and the same delta and lot
                     # size the tool's own premium levels use (signal_engine: index distance x APPROX_ATM_DELTA) -
                     # or, for a CFD, nothing but its lot (1 BTC, 100 oz of gold)
                     "strike_step": None if cfd else step, "lot_size": meta.get("lot_size"),
                     "delta": config.APPROX_ATM_DELTA,
                     "chain": None if cfd else chain_prices(feed, key, rows[-1][4], step), "written_at": now.isoformat()})
        wrote.append(key)
    return wrote


def _feed_for(market):
    """Any running feed of `market` that has candles - the always-on accounts keep one running."""
    import feeds
    keys = config.instruments_in(market)
    if not keys:
        return None
    with feeds._lock:
        running = [f for f in feeds._feeds.values() if getattr(f, "market", None) == market]
    for f in running:
        try:
            df, _ = f.candles(keys[0])
        except Exception:
            continue
        if df is not None and len(df):
            return f
    return None


def _loop():
    while True:
        for market in MARKETS:
            try:
                feed = _feed_for(market)
                if feed is not None:
                    export(feed, market=market)
            except Exception as exc:
                print(f"[kronos] {market} export failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(30)


def start():
    """The exporter thread - once per server."""
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True, name="kronos-export")
        _thread.start()


def scorecard(calls):
    """The record of scored calls: how often Kronos's direction was right, and how often the tool's own signal
    direction was right on the same calls (where it had one). Live calls and replayed ones are kept apart."""
    out = {}
    for label, rows in (("live", [c for c in calls if not c.get("replay")]),
                        ("replay", [c for c in calls if c.get("replay")])):
        trades = [c for c in rows if c.get("result")]
        # money a lot: rupees (an option, estimated with delta) or dollars (a CFD, before the spread)
        rupees = [c["rupees"] if c.get("rupees") is not None else c["usd"] for c in trades
                  if c.get("rupees") is not None or c.get("usd") is not None]
        done = [c for c in rows if c.get("actual") is not None and c.get("actual") != c.get("close")]
        k_right = sum(1 for c in done if (c["f4"] > c["close"]) == (c["actual"] > c["close"]))
        tool = [c for c in done if c.get("tool_dir")]
        t_right = sum(1 for c in tool if (c["tool_dir"] > 0) == (c["actual"] > c["close"]))
        agree = [c for c in tool if (c["tool_dir"] > 0) == (c["f4"] > c["close"])]
        out[label] = {"calls": len(rows), "checked": len(done),
                      "kronos_right": round(100 * k_right / len(done), 1) if done else None,
                      "tool_checked": len(tool),
                      "tool_right": round(100 * t_right / len(tool), 1) if tool else None,
                      "agree": round(100 * len(agree) / len(tool), 1) if tool else None,
                      # Kronos's own trades (kronos_worker.trade_from), played out over the hour
                      "trades": len(trades),
                      "targets": sum(1 for c in trades if c["result"] == "target"),
                      "stops": sum(1 for c in trades if c["result"] == "stop"),
                      "timed": sum(1 for c in trades if c["result"] == "time"),
                      "won": round(100 * sum(1 for c in trades if c["points"] > 0) / len(trades), 1) if trades else None,
                      "points": round(sum(c["points"] for c in trades), 1),
                      "rupees": round(sum(rupees)) if rupees else None, "rupees_n": len(rupees),
                      # the calls whose trade was not taken (kronos_worker.decide), by reason
                      "skipped": {r: sum(1 for c in rows if c.get("no_trade") == r)
                                  for r in sorted({c["no_trade"] for c in rows if c.get("no_trade")})}}
    return out


def public(market=None, now=None):
    """Everything the tab draws, read from the worker's files."""
    now = now or dt.datetime.now(IST)
    market = market or MARKET
    if market not in MARKETS or not config.instruments_in(market):
        return {"market": market, "note": "Kronos does not run on this market."}
    b = base()
    hb = _read_json(os.path.join(b, "worker.json"), {}) or {}
    age = None
    if hb.get("alive_at"):
        try:
            age = (now - dt.datetime.fromisoformat(hb["alive_at"])).total_seconds()
        except ValueError:
            age = None
    indices, every = [], []
    for key in config.instruments_in(market):
        inp = _read_json(os.path.join(b, "in", f"{key}.json"), {}) or {}
        fc = _read_json(os.path.join(b, "out", f"{key}.json"), None)
        calls = _read_json(os.path.join(b, "calls", f"{key}.json"), []) or []
        every += calls
        # the newest calls first - live ones, and the replays after them (marked) so day one is not empty
        live = [c for c in calls if not c.get("replay")]
        shown = (live[::-1] + [c for c in calls if c.get("replay")][::-1])[:CALLS_SHOWN]
        indices.append({"index": key, "candles": (inp.get("candles") or [])[-CHART_CANDLES:], "lot_size": inp.get("lot_size"),
                        "tool": inp.get("tool"), "forecast": fc, "score": scorecard(calls),
                        "calls": [{k: c.get(k) for k in ("t", "close", "f4", "actual", "actual_t", "tool_dir", "replay",
                                                          "result", "points", "rupees", "usd", "trade", "skip",
                                                          "taken", "no_trade")}
                                  for c in shown]})
    return {"market": market, "cfd": _is_cfd(market), "worker": {"alive_at": hb.get("alive_at"), "age_s": age,
                                         "running": age is not None and age < WORKER_STALE_S,
                                         "device": hb.get("device"), "model": hb.get("model"),
                                         "rules": hb.get("rules")},
            "indices": indices, "score": scorecard(every)}
