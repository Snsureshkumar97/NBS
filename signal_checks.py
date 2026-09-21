"""
signal_checks.py — what the AI desk looks at, laid beside the rule signal
================================================================================
Asked for by the user on 21 Sep 2026: "let the signal section compare all the
things the AI trades check". The user's choice, from three ways of doing it:
a checklist beside each signal that does NOT gate the entry, with every rule
ticket stamped with its result so it can be checked against outcomes later.

WHY IT DOES NOT GATE
    The checks the AI reads were each tested as filters on the rules over three
    years of history and none improved them in both the in-sample and the
    held-out period (gann_volume_study.py, flow_study.py); several cannot be
    tested at all (order flow, taker flow, walls). Making the rules require them
    would most likely mean fewer trades, not better ones. So the rule engine
    still enters exactly as it did; this only shows what the AI would see.

THE CHECKS - each one agrees, is against, is neutral, or has no data
    gann         the next Gann Square-of-Nine level in the trade's direction: nearer
                 than the stop is against, past the first target agrees
    volume       the volume oscillator: participation rising agrees, fading is against
    flow         Bitcoin: the 15-minute taker flow (only once the tape covers it);
                 Indian indices: the near-month future's build-up and book lean
    walls        the largest open-interest strike in the trade's way: between spot and
                 the first target is against, beyond it (or already behind) agrees
    spread       the option's bid-ask spread against the limit
    heavyweights Indian indices: the five heaviest members' futures build-up, by weight
    News is left out: it is a judgment about words, not a pass or fail.

Nothing here reads the network or writes anything: it takes what the feed
already holds. A check whose data is missing says so instead of guessing.
"""
AGREES, AGAINST, NEUTRAL, NO_DATA = "agrees", "against", "neutral", "no_data"
SYMBOL = {AGREES: "+", AGAINST: "-", NEUTRAL: "0", NO_DATA: "x"}
FLOW_MIN_PCT = 10.0             # taker flow: at least this lean, of volume, to count either way
BOOK_LEAN = 0.15                # the futures' top-five book lean that counts
HEAVY_SHARE = 0.6               # this share of the classified weight, to count either way
NOTE = ("These are the things the AI desk looks at, laid beside the rule signal. The rules do not use them to enter "
        "or skip a trade - each was tested as a filter and none improved the rules, and several cannot be tested. "
        "Every rule ticket is logged with its result so it can be compared with how the trade ended.")


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _item(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


def _num(v, dp=0):
    return f"{v:,.{dp}f}"


def _gann(rec, g, side, dp):
    label = "Gann room"
    spot, risk = _f(rec.get("spot")), _f(rec.get("risk_points"))
    lvl = _f((g or {}).get("nearest_resistance" if side == "CE" else "nearest_support"))
    if spot is None or lvl is None:
        return _item("gann", label, NO_DATA, "No Gann level without a price.")
    dist = abs(lvl - spot)
    tg = rec.get("index_targets") or []
    t1 = abs(_f(tg[0]) - spot) if tg and _f(tg[0]) is not None else None
    where = "resistance" if side == "CE" else "support"
    base = f"The next Gann {where} is {_num(lvl, dp)}, {_num(dist, dp)} points away"
    if risk and dist < risk:
        return _item("gann", label, AGAINST, f"{base}: nearer than the stop ({_num(risk, dp)} points).")
    if t1 and dist >= t1:
        return _item("gann", label, AGREES, f"{base}: past the first target ({_num(t1, dp)} points).")
    return _item("gann", label, NEUTRAL, f"{base}: beyond the stop but short of the first target.")


def _volume(g):
    label = "Volume"
    vo = ((g or {}).get("volume_oscillator") or {}).get("value_pct")
    if vo is None:
        return _item("volume", label, NO_DATA, "No volume on these candles yet.")
    if vo > 0:
        return _item("volume", label, AGREES, f"Participation is rising (oscillator {vo:+.1f}%).")
    return _item("volume", label, AGAINST, f"Participation is fading (oscillator {vo:+.1f}%).")


def _flow_crypto(flow, sign):
    label = "Taker flow"
    w = ((flow or {}).get("windows") or {}).get("15m")
    if not w or not w.get("complete") or w.get("cvd_pct_of_volume") is None:
        covers = (flow or {}).get("tape_covers_minutes")
        return _item("flow", label, NO_DATA, "The tape does not yet cover 15 minutes"
                     + (f" (it holds {covers:g})." if covers is not None else "."))
    c = w["cvd_pct_of_volume"]
    said = (flow or {}).get("in_words") or f"Takers {c:+.0f}% of volume over 15 minutes."
    if c * sign >= FLOW_MIN_PCT:
        return _item("flow", label, AGREES, said)
    if c * sign <= -FLOW_MIN_PCT:
        return _item("flow", label, AGAINST, said)
    return _item("flow", label, NEUTRAL, said)


def _flow_india(flow, sign):
    label = "Futures flow"
    fut = (flow or {}).get("index_future") or {}
    parts, notes = [], []
    try:
        import kite_flow
        lean = kite_flow.LEAN.get(fut.get("buildup_today") or "")
    except Exception:
        lean = None
    if lean:
        parts.append(1 if lean.startswith("bullish") else -1)
        notes.append(f"the day's build-up is {fut['buildup_today']}")
    bi = (fut.get("order_flow") or {}).get("book_imbalance")
    if bi is not None:
        parts.append(1 if bi >= BOOK_LEAN else -1 if bi <= -BOOK_LEAN else 0)
        notes.append(f"the top five levels lean {bi:+.2f}")
    if not notes:
        return _item("flow", label, NO_DATA, "No futures reading yet.")
    said = "The near-month future: " + ", ".join(notes) + "."
    with_side = [p * sign for p in parts if p]
    if with_side and all(p > 0 for p in with_side):
        return _item("flow", label, AGREES, said)
    if with_side and all(p < 0 for p in with_side):
        return _item("flow", label, AGAINST, said)
    return _item("flow", label, NEUTRAL, said)


def _walls(rec, side, dp):
    label = "OI wall"
    chain = rec.get("option_chain") or {}
    wall = _f(chain.get("top_call_oi_strike" if side == "CE" else "top_put_oi_strike"))
    spot = _f(rec.get("spot"))
    tg = rec.get("index_targets") or []
    t1 = _f(tg[0]) if tg else None
    kind = "call" if side == "CE" else "put"
    if wall is None or spot is None:
        return _item("walls", label, NO_DATA, "No option chain to read the largest open interest from.")
    ahead = (spot < wall) if side == "CE" else (spot > wall)
    if not ahead:
        return _item("walls", label, AGREES, f"Spot is already through the largest {kind} open interest ({_num(wall, dp)}).")
    if t1 is None:
        return _item("walls", label, NEUTRAL, f"The largest {kind} open interest is at {_num(wall, dp)}, ahead of spot.")
    blocks = (wall <= t1) if side == "CE" else (wall >= t1)
    if blocks:
        return _item("walls", label, AGAINST, f"The largest {kind} open interest ({_num(wall, dp)}) sits between spot and the first target.")
    return _item("walls", label, AGREES, f"The largest {kind} open interest ({_num(wall, dp)}) is beyond the first target.")


def _spread(rec, index):
    label = "Spread"
    pct = _f((rec.get("spread") or {}).get("pct"))
    if pct is None:
        return _item("spread", label, NO_DATA, "No live bid and offer for the contract.")
    try:
        import config
        limit = float(config.max_spread_pct(index) or 0)
    except Exception:
        limit = 0.0
    if not limit:
        return _item("spread", label, NEUTRAL, f"Spread {pct:.2f}%.")
    txt = f"Spread {pct:.2f}% against a limit of {limit:g}%."
    if pct <= limit / 2:
        return _item("spread", label, AGREES, txt)
    if pct <= limit:
        return _item("spread", label, NEUTRAL, txt)
    return _item("spread", label, AGAINST, txt)


def _heavy(flow, sign):
    label = "Heavyweights"
    w = (flow or {}).get("heavyweights_weight_pct")
    if not w:
        return None
    up, down = _f(w.get("bullish_buildup")) or 0.0, _f(w.get("bearish_buildup")) or 0.0
    if up + down <= 0:
        return _item("heavyweights", label, NO_DATA, "None of the heaviest members shows a clear build-up.")
    mine = (up if sign > 0 else down) / (up + down)
    said = f"By index weight, {up:.0f}% of the heaviest members show bullish build-up and {down:.0f}% bearish."
    if mine >= HEAVY_SHARE:
        return _item("heavyweights", label, AGREES, said)
    if mine <= 1 - HEAVY_SHARE:
        return _item("heavyweights", label, AGAINST, said)
    return _item("heavyweights", label, NEUTRAL, said)


def evaluate(rec, gann_report=None, flow=None, market="nse_index", index=None):
    """The checklist for the side the rules suggest, or None when they suggest
    none (a wait has nothing to compare against). Never raises on missing data:
    a check without its data reports no_data."""
    rec = rec or {}
    side = rec.get("option_type")
    if side not in ("CE", "PE"):
        return None
    sign = 1 if side == "CE" else -1
    dp = 0 if (index or rec.get("index")) in ("BTC", "NIFTY", "BANKNIFTY", "SENSEX") else 2
    items = [_gann(rec, gann_report, side, dp), _volume(gann_report),
             _flow_crypto(flow, sign) if market == "crypto" else _flow_india(flow, sign),
             _walls(rec, side, dp), _spread(rec, index or rec.get("index"))]
    if market != "crypto":
        h = _heavy(flow, sign)
        if h:
            items.append(h)
    n = {s: sum(1 for i in items if i["status"] == s) for s in (AGREES, AGAINST, NEUTRAL, NO_DATA)}
    parts = [f"{n[AGREES]} agree", f"{n[AGAINST]} against"]
    if n[NEUTRAL]:
        parts.append(f"{n[NEUTRAL]} neutral")
    if n[NO_DATA]:
        parts.append(f"{n[NO_DATA]} without data")
    return {"side": side, "items": items, "agree": n[AGREES], "against": n[AGAINST], "neutral": n[NEUTRAL],
            "no_data": n[NO_DATA], "summary": " · ".join(parts), "note": NOTE}


def stamp(checks):
    """(agree, against, 'gann+ volume- flow0 ...') for the trade log, or Nones."""
    if not checks:
        return None, None, None
    text = " ".join(f"{i['key']}{SYMBOL.get(i['status'], '?')}" for i in checks.get("items") or [])
    return checks.get("agree"), checks.get("against"), text
