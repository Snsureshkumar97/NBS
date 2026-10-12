#!/usr/bin/env python3
"""
kronos_worker.py — Kronos's live forecasts for the Kronos tab (kronos_live.py has the full story)
================================================================================
Runs on the server in its OWN environment, never inside the trading server (it needs PyTorch):

    ~/kronos/venv/bin/python -I ~/nbs/kronos_worker.py         (the nbs-kronos service, at the lowest priority)

The Kronos model code (github.com/shiyu-coder/Kronos, model/) and weights (Hugging Face NeoQuasar/Kronos-small and
NeoQuasar/Kronos-Tokenizer-base, MIT) live in ~/kronos, outside the repo. Every 20 seconds it looks at
<log dir>/kronos/in/<INDEX>.json (the trading server writes each index's closed 15-minute candles there) and, for a
candle it has not called yet, forecasts the next four candles - the settings kronos_study.py tested: 400 candles of
context, temperature 1.0, top-p 0.9, five samples averaged. It writes the forecast to out/<INDEX>.json, keeps every
call in calls/<INDEX>.json, and scores a call once the candle an hour later has closed (only calls whose hour stays
inside the session - the 15:15 candle's "next hour" is tomorrow morning, and that is not what is being measured).
The first time it sees an index it also replays the last two sessions' candles, marked as replays, so the record has
something in it on day one.

ITS OWN TRADE (the user, 12 Oct 2026: "the kronos also should give me strike and target however by its own code or
decision and stop loss"). Kronos forecasts candles, not trades, so each forecast is turned into one by fixed rules
read off its own output (trade_from): up -> a call, down -> a put, at the strike nearest the price; the target is
where it says the index will be in an hour; the stop is the worst point it expects on the way there (its lowest
forecast low for a call, highest forecast high for a put) - or, when that is not on the losing side of the entry,
the same distance away as the target. Played out on the next hour's real candles (trade_outcome): target, stop or
the close of the hour, in INDEX POINTS - Kronos has no option prices, so no rupees.
"""
import datetime as dt
import glob
import json
import os
import sys
import time

BASE = os.path.expanduser(os.environ.get("NBS_KRONOS_DIR") or "~/trading-tool-logs/kronos")
HOME = os.path.expanduser(os.environ.get("NBS_KRONOS_HOME") or "~/kronos")
CONTEXT, PRED_LEN, SAMPLES, TEMP, TOP_P = 400, 4, 5, 1.0, 0.9      # as kronos_study.py tested them
REPLAY = 50                     # candles replayed the first time an index is seen: the last two sessions
KEEP_CALLS = 3000
BATCH = 16
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FIRST, LAST = dt.time(9, 15), dt.time(15, 15)     # the first and last 15-minute candle of the NSE session


def parse(t):
    return dt.datetime.fromisoformat(t)


def next_slots(t, n=PRED_LEN):
    """The next n 15-minute candle starts after t on the NSE weekday schedule (09:15 to 15:15). Exchange holidays are
    not known here; these times only feed the model's clock features."""
    out = []
    while len(out) < n:
        t = t + dt.timedelta(minutes=15)
        if t.time() > LAST:
            d = t.date() + dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
            t = dt.datetime.combine(d, FIRST, tzinfo=t.tzinfo)
        out.append(t)
    return out


def score(calls, candles):
    """Fill in each unscored call's outcome from the candles: the close four candles later, when that candle has
    closed and is in the same session; a call whose hour crosses the close is marked and never scored. Returns how
    many calls changed."""
    pos = {c[0]: i for i, c in enumerate(candles)}
    changed = 0
    for c in calls:
        if c.get("actual") is not None or c.get("skip"):
            continue
        i = pos.get(c["t"])
        if i is None or i + PRED_LEN >= len(candles):
            continue
        later = candles[i + PRED_LEN]
        if parse(later[0]).date() != parse(c["t"]).date():
            c["skip"] = "the hour crosses the close"
        else:
            c["actual"], c["actual_t"] = later[4], later[0]
            if c.get("trade"):
                c["result"], c["points"] = trade_outcome(c["trade"], candles[i + 1:i + 1 + PRED_LEN])
                tr = c["trade"]
                if tr.get("premium") and c.get("lot_size"):
                    # rupees per lot, ESTIMATED: the index points x the delta the premium levels were set with, x
                    # one lot - no time decay, no spread; the option's own candles are not read here
                    c["rupees"] = round(c["points"] * tr.get("delta", 0.5) * c["lot_size"], 2)
        changed += 1
    return changed


def trade_from(close, path, step, chain=None, delta=0.5):
    """Kronos's own trade from its forecast (see the module docstring), in index levels - and, when the trading server
    sent the strike's live price from its option chain, in the option too: its premium now, and target and stop
    premiums by the tool's own rule (signal_engine: the index distance x delta, the stop never below 0.05).
    None for a flat forecast."""
    end = path[-1][4]
    if end == close:
        return None
    up = end > close
    stop = min(p[3] for p in path) if up else max(p[2] for p in path)
    if (stop >= close) if up else (stop <= close):
        stop = close - (end - close)                     # its path never goes against it: the target's distance
    side = "CE" if up else "PE"
    strike = int(round(close / step) * step) if step else None
    out = {"side": side, "strike": strike, "entry": round(close, 2), "target": round(end, 2), "stop": round(stop, 2)}
    prem = (((chain or {}).get("strikes") or {}).get(str(strike)) or {}).get(side)
    if prem:
        out.update(premium=round(float(prem), 2), delta=delta, expiry=(chain or {}).get("expiry"),
                   premium_target=round(prem + abs(end - close) * delta, 2),
                   premium_stop=round(max(0.05, prem - abs(stop - close) * delta), 2))
    return out


def trade_outcome(trade, later):
    """Play a trade on the next candles [[t, o, h, l, c], ...]: the stop first within a candle (the pessimistic
    reading - a candle does not say which came first), then the target; else out at the last candle's close.
    ("target" | "stop" | "time", points in the trade's favour)."""
    up = trade["side"] == "CE"
    e, tg, st = trade["entry"], trade["target"], trade["stop"]
    for c in later:
        hi, lo = c[2], c[3]
        if (lo <= st) if up else (hi >= st):
            return "stop", round((st - e) if up else (e - st), 2)
        if (hi >= tg) if up else (lo <= tg):
            return "target", round((tg - e) if up else (e - tg), 2)
    last = later[-1][4]
    return "time", round((last - e) if up else (e - last), 2)


def to_call(candles, i, path, tool_dir, replay, now, inp=None):
    """One call. Only the newest candle's is priced in the option - the chain sent is the price NOW; a replayed
    candle from yesterday has no option price of its own here, so its trade is in index points only."""
    inp = inp or {}
    return {"t": candles[i][0], "close": candles[i][4], "f4": path[-1][4], "path": path,
            "trade": trade_from(candles[i][4], path, inp.get("strike_step"), None if replay else inp.get("chain"),
                                inp.get("delta") or 0.5),
            "lot_size": inp.get("lot_size"),
            "tool_dir": None if replay else tool_dir, "replay": replay, "made_at": now.isoformat()}


def _read(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load():
    """The predictor - PyTorch and the model are imported only here, so the helpers above test without them."""
    import torch
    torch.set_num_threads(int(os.environ.get("NBS_KRONOS_THREADS") or 2))
    sys.path.insert(0, os.path.join(HOME, "src"))
    from model import Kronos, KronosTokenizer, KronosPredictor
    tok = KronosTokenizer.from_pretrained(os.path.join(HOME, "weights", "Kronos-Tokenizer-base"))
    mdl = Kronos.from_pretrained(os.path.join(HOME, "weights", "Kronos-small"))
    tok.eval()
    mdl.eval()
    return KronosPredictor(mdl, tok, device="cpu", max_context=512)


def forecast(pred, windows):
    """Each window (CONTEXT candles, the last one closed) -> its next PRED_LEN candles as [[t, o, h, l, c], ...]."""
    import pandas as pd
    import torch
    dfs, xts, yts = [], [], []
    for w in windows:
        dfs.append(pd.DataFrame([r[1:5] for r in w], columns=["open", "high", "low", "close"], dtype=float))
        xts.append(pd.Series(pd.to_datetime([r[0] for r in w])))
        yts.append(pd.Series(pd.to_datetime([s.isoformat() for s in next_slots(parse(w[-1][0]))])))
    torch.manual_seed(len(windows[-1]))
    res = pred.predict_batch(dfs, xts, yts, pred_len=PRED_LEN, T=TEMP, top_p=TOP_P, sample_count=SAMPLES, verbose=False)
    return [[[y.iloc[k].isoformat(), *(round(float(r[c].iloc[k]), 2) for c in ("open", "high", "low", "close"))]
             for k in range(PRED_LEN)] for r, y in zip(res, yts)]


def step(pred, now=None):
    """One pass over the indices the trading server has written: every index's newest candle first, then (once, the
    first time an index is seen) the replay of its last two sessions."""
    now = now or dt.datetime.now(IST)
    for replays in (False, True):
        for path in sorted(glob.glob(os.path.join(BASE, "in", "*.json"))):
            _one(pred, path, replays, now)


def _one(pred, path, replays, now):
    key = os.path.basename(path)[:-5]
    try:
            inp = _read(path, {}) or {}
            candles = inp.get("candles") or []
            if len(candles) < CONTEXT:
                return
            cpath = os.path.join(BASE, "calls", f"{key}.json")
            calls = _read(cpath, []) or []
            score(calls, candles)
            have = {c["t"] for c in calls}
            last = len(candles) - 1
            todo = []
            if replays:
                if not any(c.get("replay") for c in calls):
                    todo = [(i, True) for i in range(max(CONTEXT - 1, last - REPLAY), last) if candles[i][0] not in have]
            elif candles[last][0] not in have:
                todo = [(last, False)]
            for s in range(0, len(todo), BATCH):
                chunk = todo[s:s + BATCH]
                paths = forecast(pred, [candles[i - CONTEXT + 1:i + 1] for i, _ in chunk])
                for (i, replay), p in zip(chunk, paths):
                    calls.append(to_call(candles, i, p, (inp.get("tool") or {}).get("dir"), replay, now, inp))
            if todo:
                score(calls, candles)
                calls.sort(key=lambda c: c["t"])
                live = [c for c in calls if not c["replay"]]
                head = live[-1] if live else calls[-1]
                _write(os.path.join(BASE, "out", f"{key}.json"),
                       {"index": key, "from": head["t"], "close": head["close"], "path": head["path"],
                        "trade": head.get("trade"), "made_at": head["made_at"], "tool_dir": head.get("tool_dir")})
            for c in calls[:-PRED_LEN * 2]:
                c.pop("path", None)              # only the newest calls keep their whole path; the record keeps f4
            _write(cpath, calls[-KEEP_CALLS:])
    except Exception as exc:
        print(f"[kronos] {key}: {type(exc).__name__}: {exc}", flush=True)


def main():
    pred = load()
    print(f"[kronos] worker up, {BASE}", flush=True)
    while True:
        _write(os.path.join(BASE, "worker.json"), {"alive_at": dt.datetime.now(IST).isoformat(), "device": "cpu",
                                                   "model": "Kronos-small", "pid": os.getpid()})
        step(pred)
        time.sleep(20)


if __name__ == "__main__":
    main()
