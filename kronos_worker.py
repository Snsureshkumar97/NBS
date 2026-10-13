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

THE WHOLE DAY (the user, 12 Oct 2026: "if i know the whole day trend ... it will forecast till where it can go end of
the market and in options it will tell me which strike and target"). Once a session's first candle (09:15) has
closed, it also forecasts every candle left in the session - 24, to the 15:15 candle - from the same 400 candles of
context, and turns that into one option trade: up a call, down a put, at the strike nearest the price. One a day per
index, in days/<INDEX>.json, scored once the session is over. The Indian indices only - crypto never closes.
ITS EXIT (13 Oct 2026, the user: "whatever is best for option trading and exit on the profits which ever is best i
want to go ahead with that"): HELD TO THE CLOSE - no stop, no target. kronos_day_study.py replayed three years of
these forecasts, priced as the option after costs, against ten other exits (its own target and stop as first
deployed, targets with stops sized to the day's range, a stop moved to the entry, exits on the option's own price at
+30/-20 .. +100/-40); holding to the close made the most in the first two years AND the held-out final year on Nifty
and Bank Nifty (+409,486 / +213,349 a lot after costs; its forecast's own stop, a few points away, lost in the first
two years). Its forecast close is kept on the trade as where it says the day ends - shown, not traded.
THEN (13 Oct 2026, 08:40 IST, the user: "target should not be at the close target should be before the market hits
the top whatever ce or pe before that it should take the profit and come out", with "a ladder like we have it in our
tool"): the same study tested taking the profit before the top - at Kronos's best point of the day (P: its highest
forecast high for a call, lowest low for a put), 75% or 50% of the way there, a 50/75/P ladder with the tool's
ratchet, out at the candle it expects P on, and those with stops. The best of them over both periods: TARGET AT P,
NO STOP, out at the close if it is not reached (+185,512 / +208,997; holding to the close made +409,486 / +213,349).
THEN (13 Oct 2026, 08:45 IST, the user: "its not giving a stop loss and targets should be shown in option prices not
index price"): of the tested exits that take the profit before the top AND have a stop, the best was the LADDER WITH
A STOP - T1 50% of the way to P, T2 75%, T3 P; the stop half the index's average day range (last 10 sessions) from the
entry, moved to the entry once T1 is touched and to T1 once T2 is (the tool's own ratchet); out at T3, the stop, or
the close (+65,816 / +188,504; with no stop +185,512 / +208,997). That is the exit now, and a trade is scored the
moment the candles show it out - not only after the close.
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
# Which calls become TRADES (the user, 12 Oct 2026: "one trade at a time and skip small calls"). Every call is still
# made and scored on its direction; only these decide whether its trade is taken (decide()):
MIN_MOVE_PCT = 0.15             # the forecast move must be at least this share of the price (Nifty ~34 points)
MIN_RR = 1.0                    # ...and the target at least as far from the entry as the stop
LAST_TRADE = dt.time(14, 15)    # no trade from a later candle: its hour would run past the close
RULES = {"min_move_pct": MIN_MOVE_PCT, "min_rr": MIN_RR, "last_trade": LAST_TRADE.strftime("%H:%M"),
         "one_at_a_time": True}


def rules_for(market):
    """The rules above for one market. Bitcoin and gold (crypto, 12 Oct 2026) have no session close to stop short of -
    Bitcoin never stops, gold's daily break is caught from the candles instead (score())."""
    return dict(RULES, last_trade=None) if market == "crypto" else RULES
KEEP_CALLS = 3000
BATCH = 16
DAY_REPLAY = 10                 # sessions whose whole day is replayed the first time an index is seen
DAY_BATCH = 4                   # whole-day forecasts are six times as long: fewer at once, a heartbeat between
DAY_PATHS = 10                  # the newest day calls keep their whole forecast path
DAY_EXIT = "ladder"             # T1 50% / T2 75% / T3 its best point of the day, a stop that ratchets, else out at
                                # the close (kronos_day_study.py, 13 Oct 2026 - "a stop loss", "before the top")
DAY_STOP_RANGE = 0.5            # the stop: this share of the index's average day range from the entry
RANGE_DAYS = 10                 # ...over the sessions before the trade's
KEEP_DAYS = 400
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FIRST, LAST = dt.time(9, 15), dt.time(15, 15)     # the first and last 15-minute candle of the NSE session


def parse(t):
    return dt.datetime.fromisoformat(t)


def next_slots(t, n=PRED_LEN, continuous=False):
    """The next n 15-minute candle starts after t: on the NSE weekday schedule (09:15 to 15:15), or every 15 minutes
    round the clock for a market that does not close (crypto). Exchange holidays are not known here; these times only
    feed the model's clock features."""
    out = []
    while len(out) < n:
        t = t + dt.timedelta(minutes=15)
        if continuous:
            out.append(t)
            continue
        if t.time() > LAST:
            d = t.date() + dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
            t = dt.datetime.combine(d, FIRST, tzinfo=t.tzinfo)
        out.append(t)
    return out


def day_len(t):
    """How many 15-minute candles follow candle t in its own session, to the 15:15 one: 24 from the 09:15 candle."""
    t = parse(t) if isinstance(t, str) else t
    end = dt.datetime.combine(t.date(), LAST, tzinfo=t.tzinfo)
    return max(0, int((end - t).total_seconds() // 900))


def session_rest(candles, i):
    """The candles after candle i in its own session (the same date)."""
    d = parse(candles[i][0]).date()
    out = []
    for c in candles[i + 1:]:
        if parse(c[0]).date() != d:
            break
        out.append(c)
    return out


def session_done(candles, i):
    """Is candle i's session over in these candles - its 15:15 candle has closed, or a later session has begun?"""
    d = parse(candles[i][0]).date()
    for c in candles[i + 1:]:
        tc = parse(c[0])
        if tc.date() != d or tc.time() >= LAST:
            return True
    return False


def score(calls, candles):
    """Fill in each unscored call's outcome from the candles: the close four candles later, when that candle has
    closed and follows on WITHOUT A GAP (exactly an hour after the call's own candle); a call whose hour crosses the
    close (the Indian indices) or a break in trading (gold's daily break, a weekend) is marked and never scored.
    Returns how many calls changed."""
    pos = {c[0]: i for i, c in enumerate(candles)}
    changed = 0
    for c in calls:
        if c.get("actual") is not None or c.get("skip"):
            continue
        i = pos.get(c["t"])
        if i is None or i + PRED_LEN >= len(candles):
            continue
        later = candles[i + PRED_LEN]
        if parse(later[0]) - parse(c["t"]) != dt.timedelta(minutes=15 * PRED_LEN):
            c["skip"] = "the hour crosses the close or a break in trading"
        else:
            c["actual"], c["actual_t"] = later[4], later[0]
            if c.get("trade") and c.get("taken"):
                c["result"], c["points"] = trade_outcome(c["trade"], candles[i + 1:i + 1 + PRED_LEN])
                tr = c["trade"]
                if tr.get("cfd") and c.get("lot_size"):
                    # dollars a lot for a CFD (Exness): the points x the lot (1 BTC, 100 oz) - before the spread
                    c["usd"] = round(c["points"] * c["lot_size"], 2)
                elif tr.get("premium") and c.get("lot_size"):
                    # rupees per lot, ESTIMATED: the index points x the delta the premium levels were set with, x
                    # one lot - no time decay, no spread; the option's own candles are not read here
                    c["rupees"] = round(c["points"] * tr.get("delta", 0.5) * c["lot_size"], 2)
        changed += 1
    return changed


def day_range(candles, t):
    """The index's average day range (the session's high - low) over the RANGE_DAYS sessions before candle t's, or
    None when there are not that many."""
    d0 = parse(t).date()
    by = {}
    for c in candles:
        d = parse(c[0]).date()
        if d >= d0:
            continue
        h, l = by.get(d, (c[2], c[3]))
        by[d] = (max(h, c[2]), min(l, c[3]))
    days = sorted(by)[-RANGE_DAYS:]
    return sum(by[d][0] - by[d][1] for d in days) / len(days) if len(days) == RANGE_DAYS else None


def day_trade(t, hi, lo, rng=None):
    """A whole-day trade as kronos_day_study.py chose it: a LADDER to Kronos's best point of the day (P: hi - its
    highest forecast high - for a call, lo for a put) - T1 50% of the way, T2 75%, T3 (target) P - and a stop
    DAY_STOP_RANGE x the day range rng from the entry, which ladder_walk moves to the entry at T1 and to T1 at T2. Its
    forecast close stays as close_fc. The option's premium at each level by the tool's own rule (the index distance x
    delta; a stop premium never below 0.05)."""
    if not t:
        return t
    up = t["side"] == "CE"
    e = t["entry"]
    fc = t.get("close_fc", t["target"])
    P = hi if up else lo
    if P is None or ((P <= e) if up else (P >= e)):
        P = fc                                           # no best point on its side: its forecast close
    stop = round(e - (1 if up else -1) * DAY_STOP_RANGE * rng, 2) if rng else None
    t = dict(t, exit=DAY_EXIT, stop=stop, close_fc=fc, target=round(P, 2), t1=round(e + 0.5 * (P - e), 2),
             t2=round(e + 0.75 * (P - e), 2))
    t.pop("premium_stop", None)
    if t.get("premium"):
        d = t.get("delta", 0.5)
        for k in ("t1", "t2", "target"):
            t["premium_" + k] = round(t["premium"] + abs(t[k] - e) * d, 2)
        if stop is not None:
            t["premium_stop"] = round(max(0.05, t["premium"] - abs(stop - e) * d), 2)
    return t


def ladder_walk(tr, later):
    """A ladder trade (day_trade) on the candles after its entry, as kronos_day_study.walk_ladder plays it: the stop
    first within a candle, then T3 (the target); T1 touched moves the stop to the entry, T2 to T1. ->
    (result "target" | "stop" | None while still open, the level it went out at, the candle number, the stop now)."""
    up = tr["side"] == "CE"
    e = tr["entry"]
    stop = tr.get("stop")
    better = max if up else min
    hit1 = hit2 = False
    for j, c in enumerate(later):
        hi, lo = c[2], c[3]
        if stop is not None and ((lo <= stop) if up else (hi >= stop)):
            return "stop", stop, j, stop
        if (hi >= tr["target"]) if up else (lo <= tr["target"]):
            return "target", tr["target"], j, stop
        if not hit1 and ((hi >= tr["t1"]) if up else (lo <= tr["t1"])):
            hit1 = True
            stop = e if stop is None else better(stop, e)
        if not hit2 and ((hi >= tr["t2"]) if up else (lo <= tr["t2"])):
            hit2 = True
            stop = better(stop, tr["t1"])
    return None, None, None, stop


def score_days(days, candles):
    """Each unscored whole-day call's outcome once its session is over: where the index closed the day, and its trade
    (if taken) played on the rest of the session's real candles - target, stop, or out at the close. Returns how many
    changed."""
    pos = {c[0]: i for i, c in enumerate(candles)}
    changed = 0
    for c in days:
        tr = c.get("trade")
        if tr and c.get("taken") and tr.get("exit") == "ladder" and c.get("result") is None and not c.get("skip"):
            i = pos.get(c["t"])
            if i is not None:
                rest = session_rest(candles, i)
                res, lvl, j, _ = ladder_walk(tr, rest)
                if res is None and rest and session_done(candles, i):
                    res, lvl, j = "close", rest[-1][4], len(rest) - 1
                if res:
                    up = tr["side"] == "CE"
                    c["result"], c["points"], c["exit_t"] = res, round((lvl - tr["entry"]) * (1 if up else -1), 2), rest[j][0]
                    if tr.get("premium") and c.get("lot_size"):
                        c["rupees"] = round(c["points"] * tr.get("delta", 0.5) * c["lot_size"], 2)
                    changed += 1
        if c.get("actual") is not None or c.get("skip"):
            continue
        i = pos.get(c["t"])
        if i is None or not session_done(candles, i):
            continue
        rest = session_rest(candles, i)
        if not rest:
            c["skip"] = "no candles after it that day"
        else:
            c["actual"], c["actual_t"] = rest[-1][4], rest[-1][0]
            if c.get("trade") and c.get("taken") and c["trade"].get("exit") != "ladder":
                tr = c["trade"]
                if tr.get("exit") in ("close", "peak"):  # its target (if any) on the way, else the day's last close
                    up = tr["side"] == "CE"
                    hit = tr.get("exit") == "peak" and any((x[2] >= tr["target"]) if up else (x[3] <= tr["target"])
                                                           for x in rest)
                    move = (tr["target"] if hit else rest[-1][4]) - tr["entry"]
                    c["result"], c["points"] = ("target" if hit else "close"), round(move if up else -move, 2)
                else:
                    c["result"], c["points"] = trade_outcome(tr, rest)
                if tr.get("premium") and c.get("lot_size"):
                    c["rupees"] = round(c["points"] * tr.get("delta", 0.5) * c["lot_size"], 2)
        changed += 1
    return changed


def decide_day(c):
    """Whether a whole-day call's trade is taken: not flat, and the move it forecasts to the CLOSE at least
    MIN_MOVE_PCT of the price (the tab's "skip small calls" - the entries kronos_day_study.py tested). With no stop
    there is no reward to weigh against a risk. One a day, so nothing is ever still open."""
    t = c.get("trade")
    why = None
    if not t:
        why = "a flat forecast"
    elif abs(t.get("close_fc", t["target"]) - t["entry"]) / t["entry"] * 100 < MIN_MOVE_PCT:
        why = "a small move"
    elif not t.get("exit") and t.get("stop") is not None and abs(t["target"] - t["entry"]) < MIN_RR * abs(t["stop"] - t["entry"]):
        why = "reward smaller than risk"
    c["taken"] = why is None
    if why:
        c["no_trade"] = why
    return c["taken"]


def _closed_by(trade, candles, i0, i):
    """Has the trade entered at candle i0's close ENDED by the close of candle i? At its target or stop on one of the
    candles i0+1..i, or because its hour (PRED_LEN candles) is over."""
    up = trade["side"] == "CE"
    for c in candles[i0 + 1:min(i, i0 + PRED_LEN) + 1]:
        if ((c[3] <= trade["stop"]) if up else (c[2] >= trade["stop"])) or \
                ((c[2] >= trade["target"]) if up else (c[3] <= trade["target"])):
            return True
    return i >= i0 + PRED_LEN


def decide(calls, candles, market="nse_index"):
    """Which calls' trades are TAKEN, oldest first, by RULES above: a trade worth taking (the forecast move at least
    MIN_MOVE_PCT of the price, the reward at least MIN_RR x the risk, not after LAST_TRADE) and no trade of this index
    still open. Each call gets "taken" True/False and, when not taken, "no_trade" with the reason. A call already
    decided stays decided. The live calls and the replayed ones are two separate runs, each one trade at a time.
    Returns how many were decided now."""
    pos = {c[0]: i for i, c in enumerate(candles)}
    order = sorted(calls, key=lambda c: c["t"])
    taken_last = {}                                   # replay or not -> (call, candle number) of its last taken trade
    n = 0
    for c in order:
        i = pos.get(c["t"])
        run = bool(c.get("replay"))
        last_taken = taken_last.get(run)
        if "taken" in c:
            if c["taken"] and i is not None:
                taken_last[run] = (c, i)
            continue
        if i is None:
            continue
        t = c.get("trade")
        why = None
        if not t:
            why = "a flat forecast"
        elif market != "crypto" and parse(c["t"]).time() > LAST_TRADE:
            why = "too near the close"
        elif abs(t["target"] - t["entry"]) / t["entry"] * 100 < MIN_MOVE_PCT:
            why = "a small move"
        elif abs(t["target"] - t["entry"]) < MIN_RR * abs(t["stop"] - t["entry"]):
            why = "reward smaller than risk"
        elif last_taken and not _closed_by(last_taken[0]["trade"], candles, last_taken[1], i):
            why = "a trade still open"
        c["taken"] = why is None
        if why:
            c["no_trade"] = why
            for k in ("result", "points", "rupees", "usd"):
                c.pop(k, None)
        else:
            taken_last[run] = (c, i)
        n += 1
    return n


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
    trade = trade_from(candles[i][4], path, inp.get("strike_step"), None if replay else inp.get("chain"),
                       inp.get("delta") or 0.5)
    if trade and inp.get("cfd"):
        trade = dict(trade, cfd=True, strike=None)        # the instrument itself, bought or sold - no option
    return {"t": candles[i][0], "close": candles[i][4], "f4": path[-1][4], "path": path,
            "trade": trade,
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


def forecast(pred, windows, continuous=False, n=PRED_LEN):
    """Each window (CONTEXT candles, the last one closed) -> its next n candles as [[t, o, h, l, c], ...]."""
    import pandas as pd
    import torch
    dfs, xts, yts = [], [], []
    for w in windows:
        dfs.append(pd.DataFrame([r[1:5] for r in w], columns=["open", "high", "low", "close"], dtype=float))
        xts.append(pd.Series(pd.to_datetime([r[0] for r in w])))
        yts.append(pd.Series(pd.to_datetime([s.isoformat() for s in next_slots(parse(w[-1][0]), n, continuous=continuous)])))
    torch.manual_seed(len(windows[-1]))
    res = pred.predict_batch(dfs, xts, yts, pred_len=n, T=TEMP, top_p=TOP_P, sample_count=SAMPLES, verbose=False)
    return [[[y.iloc[k].isoformat(), *(round(float(r[c].iloc[k]), 2) for c in ("open", "high", "low", "close"))]
             for k in range(n)] for r, y in zip(res, yts)]


def beat():
    """The heartbeat the tab reads to say the worker is running - also written between long pieces of work."""
    _write(os.path.join(BASE, "worker.json"), {"alive_at": dt.datetime.now(IST).isoformat(), "device": "cpu",
                                               "model": "Kronos-small", "pid": os.getpid(), "rules": RULES})


def step(pred, now=None):
    """One pass over the indices the trading server has written: every index's newest candle first, then the
    whole-day forecasts, then (once, the first time an index is seen) the replays."""
    now = now or dt.datetime.now(IST)
    for replays in (False, True):
        files = sorted(glob.glob(os.path.join(BASE, "in", "*.json")))
        for path in files:
            _one(pred, path, replays, now)
            beat()
        for path in files:
            _day(pred, path, replays, now)
            beat()


def _one(pred, path, replays, now):
    key = os.path.basename(path)[:-5]
    try:
            inp = _read(path, {}) or {}
            candles = inp.get("candles") or []
            if len(candles) < CONTEXT:
                return
            market = inp.get("market") or "nse_index"
            cont = market == "crypto"
            cpath = os.path.join(BASE, "calls", f"{key}.json")
            calls = _read(cpath, []) or []
            decide(calls, candles, market)
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
                paths = forecast(pred, [candles[i - CONTEXT + 1:i + 1] for i, _ in chunk], cont)
                for (i, replay), p in zip(chunk, paths):
                    calls.append(to_call(candles, i, p, (inp.get("tool") or {}).get("dir"), replay, now, inp))
                beat()
            if todo:
                decide(calls, candles, market)
                score(calls, candles)
            calls.sort(key=lambda c: c["t"])
            live = [c for c in calls if not c["replay"]]
            head = live[-1] if live else (calls[-1] if calls else None)
            if head and head.get("path"):            # rewritten every pass: a decision can land after the forecast
                _write(os.path.join(BASE, "out", f"{key}.json"),
                       {"index": key, "from": head["t"], "close": head["close"], "path": head["path"],
                        "trade": head.get("trade"), "taken": head.get("taken"), "no_trade": head.get("no_trade"),
                        "made_at": head["made_at"], "tool_dir": head.get("tool_dir"), "rules": rules_for(market),
                        "market": market, "cfd": bool(inp.get("cfd"))})
            for c in calls[:-PRED_LEN * 2]:
                c.pop("path", None)              # only the newest calls keep their whole path; the record keeps f4
            _write(cpath, calls[-KEEP_CALLS:])
    except Exception as exc:
        print(f"[kronos] {key}: {type(exc).__name__}: {exc}", flush=True)


def _day(pred, path, replays, now):
    """The whole-day forecast of one index: from its newest session's 09:15 candle while that session is still on
    (priced in the option from the chain sent with it - unless the worker reached it late, after a later candle had
    closed, when the chain is no longer the price at 09:30 and the trade stays in index points), or, once, the
    replay of its last DAY_REPLAY finished sessions."""
    key = os.path.basename(path)[:-5]
    try:
        inp = _read(path, {}) or {}
        if (inp.get("market") or "nse_index") != "nse_index":
            return                                       # crypto never closes: there is no "whole day" to forecast
        candles = inp.get("candles") or []
        if len(candles) < CONTEXT:
            return
        dpath = os.path.join(BASE, "days", f"{key}.json")
        days = _read(dpath, []) or []
        have = {c["t"] for c in days}
        firsts = [i for i in range(CONTEXT - 1, len(candles)) if parse(candles[i][0]).time() == FIRST]
        todo = []
        if replays:
            if not any(c.get("replay") for c in days):
                todo = [(i, True) for i in firsts if session_done(candles, i) and candles[i][0] not in have][-DAY_REPLAY:]
        elif firsts and not session_done(candles, firsts[-1]) and candles[firsts[-1]][0] not in have:
            todo = [(firsts[-1], False)]
        changed = False
        for s in range(0, len(todo), DAY_BATCH):
            chunk = todo[s:s + DAY_BATCH]
            n = day_len(candles[chunk[0][0]][0])
            paths = forecast(pred, [candles[i - CONTEXT + 1:i + 1] for i, _ in chunk], False, n)
            for (i, replay), p in zip(chunk, paths):
                late = not replay and i != len(candles) - 1
                c = to_call(candles, i, p, (inp.get("tool") or {}).get("dir"), replay, now,
                            dict(inp, chain=None) if late else inp)
                c.update(kind="day", hi=max(q[2] for q in p), lo=min(q[3] for q in p))
                c["trade"] = day_trade(c["trade"], c["hi"], c["lo"], day_range(candles, c["t"]))
                if late:
                    c["late"] = True
                    c["tool_dir"] = None                 # the tool's direction sent is today's NOW, not at 09:30
                days.append(c)
            changed = True
            beat()
        for c in days:
            if c.get("trade") and c["trade"].get("exit") != DAY_EXIT:
                # made under an earlier exit (its forecast's own target and stop; held to the close): re-decided and
                # re-scored under this one, so the record is one rule throughout
                c["trade"] = day_trade(c["trade"], c.get("hi"), c.get("lo"), day_range(candles, c["t"]))
                for k in ("taken", "no_trade", "result", "points", "rupees", "actual", "actual_t", "exit_t"):
                    c.pop(k, None)
            if "taken" not in c:
                decide_day(c)
                changed = True
        if score_days(days, candles):
            changed = True
        if changed:
            days.sort(key=lambda c: c["t"])
            for c in days[:-DAY_PATHS]:
                c.pop("path", None)
            _write(dpath, days[-KEEP_DAYS:])
    except Exception as exc:
        print(f"[kronos] {key} day: {type(exc).__name__}: {exc}", flush=True)


def main():
    pred = load()
    print(f"[kronos] worker up, {BASE}", flush=True)
    while True:
        beat()
        step(pred)
        time.sleep(20)


if __name__ == "__main__":
    main()
