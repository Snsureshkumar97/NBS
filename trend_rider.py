"""
trend_rider.py — the "Structural Trend Rider" as a live system on an Indian index
================================================================================
The user, 7 Oct 2026, after pasted_combos_study.py / trend_rider_sweep.py / mix_study.py: "so apply for which index
best apply for that". Each index trades on ONE system, chosen per index (TicketBook.system_for, the Dashboard's System
selector); config.SYSTEM_DEFAULTS starts Nifty and Bank Nifty on this one, Sensex and Midcap on the tool's own rules.

THE RULES - exactly those measured (pasted_combos_study.signals / run, combination 2, 15-minute candles):
  long  (buy CE): the close above EMA 50 AND above VWAP, ADX(14) > adx_min AND rising, +DI above -DI
  short (buy PE): the close below EMA 50 AND below VWAP, ADX(14) > adx_min AND rising, -DI above +DI
  entered on the FIRST closed candle where all of it holds (a fresh signal - not again while it stays true), decided
  only in the first ENTRY_WINDOW_S after that close (the test entered at the close)
  stop: the lowest low (highest high) of the last `swing` closed candles; ONE target at target_r x the risk; no T1
  step-up, Supertrend trail, 2-hour breakeven or reversal exit - but since 8 Oct 2026 the stop steps up near the target
  (config.NEAR_TARGET_STEPS: 90% of the way to T2 -> stop to T1, 90% of the way to T3 -> stop to T2, T1/T2/T3 being a
  third, two thirds and all of the target; trend_rider_t1_study.py); out at the day's close. config.TREND_RIDER holds adx_min 20, swing 5, target_r 2.75 - the middle of the 12-of-60 block of
  settings that beat the tool's own system in both test periods (trend_rider_sweep.py).

It reaches the ticket book through the entry-rule path built for the Exness rules (tickets._consider_rule): one decision
per closed 15-minute candle, one position at a time, a complete direction + stop + target or nothing; the option
fields stay (an index option, unlike a CFD): the strike at the money, its live premium, and a premium stop and target
from the index levels at ~0.5 delta, as the engine's own ladder.
"""
import numpy as np
import pandas as pd

import config
import indicators as ind

BAR = pd.Timedelta(minutes=15)
ENTRY_WINDOW_S = 120
MIN_CLOSED = 80                  # EMA 50 and ADX(14) warmed up
_CACHE = {}
_LAST_READY = {}


def params():
    p = {"adx_min": 20.0, "swing": 5, "target_r": 2.75}
    p.update(getattr(config, "TREND_RIDER", {}) or {})
    return p


def states(df, adx_min):
    """The conditions on every candle of `df` (closed candles): the arrays pasted_combos_study.signals(df, 2) used."""
    c = df["Close"]
    e50 = ind.ema(c, 50)
    vw = ind.vwap(df)
    adx = ind.adx(df, 14)
    pdi, mdi = ind.plus_minus_di(df, 14)
    rising = adx > adx.shift(1)
    strong = (adx > adx_min) & rising
    return {"long": (c > e50) & (c > vw) & strong & (pdi > mdi), "short": (c < e50) & (c < vw) & strong & (mdi > pdi),
            "e50": e50, "vw": vw, "adx": adx, "pdi": pdi, "mdi": mdi, "rising": rising}


def evaluate(index_key, df, now=None):
    """The reading on the last CLOSED candle of `df` (the last row is the forming candle): {ready, side (+1/-1/0),
    stop, close, bar_close, fresh, values, conds}. A candle counts as closed only when `df` already holds the next one."""
    p = params()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if df is None or len(df) < 3:
        return {"ready": False, "why": "no candles yet"}
    if df.index[-1] + BAR <= now:
        prev = _LAST_READY.get(index_key)
        why = "waiting for the new 15-minute candle's first prices"
        return dict(prev, ready=False, why=why, side=0, fresh=False) if prev else {"ready": False, "why": why}
    closed = df.iloc[:-1][["Open", "High", "Low", "Close", "Volume"]]
    if len(closed) < MIN_CLOSED:
        return {"ready": False, "why": f"only {len(closed)} closed candles - {MIN_CLOSED} needed"}
    key = (index_key, closed.index[-1], float(closed["Close"].iloc[-1]), len(closed), p["adx_min"], p["swing"])
    out = _CACHE.get(key)
    if out is None:
        s = states(closed, p["adx_min"])
        L, S = bool(s["long"].iloc[-1]), bool(s["short"].iloc[-1])
        Lp, Sp = bool(s["long"].iloc[-2]), bool(s["short"].iloc[-2])
        side = 1 if (L and not Lp) else -1 if (S and not Sp) else 0
        w = int(p["swing"])
        lo, hi = float(closed["Low"].iloc[-w:].min()), float(closed["High"].iloc[-w:].max())
        c = float(closed["Close"].iloc[-1])
        num = lambda x: float(x) if x == x else None
        values = {"close": c, "ema50": num(s["e50"].iloc[-1]), "vwap": num(s["vw"].iloc[-1]), "adx": num(s["adx"].iloc[-1]),
                  "adx_prev": num(s["adx"].iloc[-2]), "pdi": num(s["pdi"].iloc[-1]), "mdi": num(s["mdi"].iloc[-1]),
                  "swing_low": lo, "swing_high": hi, "holding": 1 if L else -1 if S else 0}
        out = {"ready": True, "side": side, "stop": lo if side > 0 else hi if side < 0 else None, "close": c,
               "bar_close": (closed.index[-1] + BAR).isoformat(), "values": values}
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = out
    out = dict(out, fresh=(now - pd.Timestamp(out["bar_close"])).total_seconds() <= ENTRY_WINDOW_S)
    _LAST_READY[index_key] = out
    return out


def _conditions(ev, p):
    """The card's rows: each condition for the side the market leans to (above EMA 50 -> the call's conditions)."""
    v = (ev or {}).get("values") or {}
    if not (ev or {}).get("ready") or v.get("ema50") is None:
        return [], 0
    lean = 1 if v["close"] > v["ema50"] else -1
    up = lean > 0
    adx, prev = v.get("adx"), v.get("adx_prev")
    rows = [
        ("tr_ema50", "EMA 50", (v["close"] > v["ema50"]) if up else (v["close"] < v["ema50"]),
         f"{v['ema50']:,.2f}",
         (f"Price {v['close']:,.2f} is above EMA 50 ({v['ema50']:,.2f}) - the longer trend is up.",
          f"Price {v['close']:,.2f} is not above EMA 50 ({v['ema50']:,.2f}).") if up else
         (f"Price {v['close']:,.2f} is below EMA 50 ({v['ema50']:,.2f}) - the longer trend is down.",
          f"Price {v['close']:,.2f} is not below EMA 50 ({v['ema50']:,.2f}).")),
        ("tr_vwap", "VWAP", (v["close"] > v["vwap"]) if up else (v["close"] < v["vwap"]), f"{v['vwap']:,.2f}",
         (f"Price is above today's VWAP ({v['vwap']:,.2f}).", f"Price is not above today's VWAP ({v['vwap']:,.2f}).") if up else
         (f"Price is below today's VWAP ({v['vwap']:,.2f}).", f"Price is not below today's VWAP ({v['vwap']:,.2f}).")),
        ("tr_adx", f"ADX > {p['adx_min']:g}", adx is not None and adx > p["adx_min"], "—" if adx is None else f"{adx:.1f}",
         (f"ADX {adx:.1f} is over {p['adx_min']:g} - a trend, not a range." if adx is not None else "",
          f"ADX {adx:.1f} is not over {p['adx_min']:g} - no trend strong enough." if adx is not None else "No ADX yet.")),
        ("tr_rising", "ADX rising", adx is not None and prev is not None and adx > prev,
         "—" if adx is None or prev is None else f"{prev:.1f} → {adx:.1f}",
         ("ADX is rising - the trend is getting stronger.", "ADX is not rising.")),
        ("tr_di", "+DI / -DI", (v["pdi"] > v["mdi"]) if up else (v["mdi"] > v["pdi"]),
         "—" if v.get("pdi") is None else f"{v['pdi']:.1f} / {v['mdi']:.1f}",
         ("+DI is over -DI - buyers are pushing harder." , "+DI is not over -DI.") if up else
         ("-DI is over +DI - sellers are pushing harder.", "-DI is not over +DI.")),
    ]
    return rows, lean


def apply(rec, ev, index_key, avoid_strikes=None):
    """Write the reading into the engine's recommendation for this index: the card's conditions, and - on a fresh
    signal - the side, the strike, its premium, the stop and the one target a ticket freezes. Anything incomplete is no
    trade, said in words."""
    import signal_engine as se
    p = params()
    rows, lean = _conditions(ev, p)
    info = {"label": "Trend Rider", "system": "trend_rider", "ready": bool((ev or {}).get("ready")), "why": (ev or {}).get("why"),
            "side": 0, "bar_close": (ev or {}).get("bar_close"), "fresh": bool((ev or {}).get("fresh")),
            "values": (ev or {}).get("values") or {}, "votes": [], "no_cooldown": True,
            "filters": [{"key": k, "name": n, "ok": ok, "reading": rd, "texts": t} for k, n, ok, rd, t in rows],
            "target_r": p["target_r"], "swing": p["swing"], "stop_atr": None,
            "tag": "Trend Rider · every condition holds",
            "note": (f"Trend Rider: a trade on the FIRST 15-minute close where price is above EMA 50 and VWAP, ADX is over "
                     f"{p['adx_min']:g} and rising, and +DI is over -DI (the mirror image for a put) - decided once, at "
                     f"that close. Stop at the last {p['swing']} candles' low (high), one target at {p['target_r']:g} x "
                     "the risk; near T2 the stop moves to T1, near T3 to T2.")}
    rec["rule"] = info
    side = (ev or {}).get("side", 0) if info["ready"] else 0
    spot, stop = rec.get("spot"), (ev or {}).get("stop")
    ok_levels = bool(side) and spot is not None and stop is not None and ((stop < spot) if side > 0 else (stop > spot))
    if ok_levels:
        R = abs(spot - stop)
        T = p["target_r"] * R
        opt = "CE" if side > 0 else "PE"
        oi = rec.get("option_chain") or {}
        strike, swap, taken = rec.get("atm_strike"), None, False
        if avoid_strikes and oi.get("available") and oi.get("strikes"):
            listed = [s_["strike"] for s_ in oi["strikes"] if s_.get("strike") is not None]
            want = min(listed, key=lambda k: abs(k - strike)) if listed else strike
            free, moved = se._next_free_strike(oi, want, opt, {(float(k), t) for k, t in avoid_strikes})
            if free is None:
                taken = True
            elif moved:
                swap, strike = {"from": want, "to": free}, free
        ltp = se._find_strike_ltp(oi, strike, opt) if strike is not None else None
        targets = [round(spot + side * T * f / 3, 2) for f in (1, 2, 3)]
        d = config.APPROX_ATM_DELTA
        if ltp:
            ptg = [round(ltp + abs(t - spot) * d, 2) for t in targets]
            psl, src = round(max(0.05, ltp - R * d), 2), "live"
        else:
            ptg = [round(abs(t - spot) * d, 1) for t in targets]
            psl, src = round(R * d, 1), "approx_move"
        word = "BUY CE (Call)" if side > 0 else "BUY PE (Put)"
        rec.update(bias="BULLISH" if side > 0 else "BEARISH", option_type=opt, suggested_strike=strike,
                   strike_swap=swap, strike_taken=taken, live_ltp=ltp, spread=se._find_strike_quote(oi, strike, opt),
                   index_stop_loss=round(float(stop), 2), risk_points=round(R, 2), index_targets=targets,
                   premium_targets=ptg, premium_stop_loss=psl, premium_source=src, target_basis="rule",
                   gate_targets=None, reach_to_risk=p["target_r"], reach_points=None, sl_basis="trend_rider",
                   confidence="Rule", action=f"{word} - Trend Rider: every condition holds, first time, on the 15-minute close",
                   blockers=[], adx_blocked=False, macd_blocked=False, not_worth_it=False)
        info["side"] = side
    else:
        why = _why_not(ev, rows, lean, side, spot, stop)
        rec.update(bias="NEUTRAL", option_type=None, index_stop_loss=None, risk_points=None, reach_to_risk=None,
                   reach_points=None, index_targets=[None, None, None], premium_targets=[None, None, None],
                   premium_stop_loss=None, premium_source=None, target_basis="rule", gate_targets=None,
                   confidence="N/A", action="NO TRADE - WAIT (Trend Rider: " + "; ".join(w.rstrip(".") for w in why) + ")",
                   blockers=why, adx_blocked=False, macd_blocked=False, not_worth_it=False)
    return rec


def _why_not(ev, rows, lean, side, spot, stop):
    if not (ev or {}).get("ready"):
        return [f"Waiting - {(ev or {}).get('why') or 'no reading yet'}."]
    if side and (spot is None or stop is None):
        return ["No price to measure the stop from yet."]
    if side:
        return [f"The signal fired at the close but price has already gone past its stop ({stop:,.2f}) - no trade."]
    missing = [n for _, n, ok, _, _ in rows if not ok]
    if missing:
        return [f"Not every condition holds for a {'call' if lean > 0 else 'put'}: {', '.join(missing)} not yet."]
    return ["Every condition still holds, but it first held on an earlier close - the Trend Rider enters only on the "
            "first one. It waits for the next fresh signal."]
