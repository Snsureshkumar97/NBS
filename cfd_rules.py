"""
cfd_rules.py — the Exness BTC / gold entry rules chosen by cfd_vote_search.py, live
================================================================================
The user, 4 Oct 2026: "find a startegy that have good win rate and low loss mix and match all
startegies ... bring out the best one" -> cfd_vote_search.py tried ~1,200 combinations of 22
votes and 7 filters per market on Exness's real ticks (picked on the in-sample years only,
then checked once on the held-out year) -> the user chose, for BOTH markets, the
"accuracy-first" rule (config.CFD_RULES):

  BTC   candle + ha agree, vol_rising                      stop 3 x ATR, target 0.75 x the stop
        in-sample 59% won, +77,886 per BTC; held-out 59% won, +18,644
  GOLD  d1_trend + h1_trend + roc12 agree, adx_rising, vol_rising   stop 3 x ATR, target 0.75 x
        in-sample 62% won, +108,722 per lot; held-out 59% won, +47,774

HOW IT IS TRADED - exactly as tested, nothing the test did not have:
  * decided ONCE, on a CLOSED 15-minute candle, and only in the first RULE_ENTRY_WINDOW_S
    after its close (the test entered at the close);
  * a buy when every vote says buy and every filter is true; a sell the mirror image;
  * the stop at stop_atr x ATR(14) from the entry, the target at target_r x that distance -
    one target, nothing moves the stop on the way (no T1 step, no trail, no reversal exit);
  * one position at a time, REENTRY_COOLDOWN_MIN after an exit, out after 24 hours.

THE VOTES are computed here with the very formulas cfd_vote_search.votes_and_filters() used
(cfd_rules_test.py checks them bar for bar on the 3-year history).

4 Oct 2026: BTC's rule is now RSI-2 + the stochastic (config.CFD_RULES), on a FORWARD TEST on the
demo account - the candle rule failed a coin-flip check and cfd_strict_search.py found nothing that
passes; RSI-2 came closest. Then, the same day, its 87% version (one close target, cfd_strict_search.py
--close): RSI-2 with ADX >= 25, target 0.2 x the stop. Not proven: the card says so.
"""
import numpy as np
import pandas as pd

import config

BAR = pd.Timedelta(minutes=15)
RULE_ENTRY_WINDOW_S = 120
HISTORY_DAYS = 30            # enough for EMA200 / the recursive Heikin-Ashi to forget their start
LABELS = {"candle": "Candle colour", "ha": "Heikin-Ashi", "d1_trend": "Day trend", "h1_trend": "Hour trend",
          "roc12": "Momentum (3h)", "vol_rising": "Volume rising", "adx_rising": "ADX rising",
          "rsi2": "RSI-2 extreme", "stoch50": "Stochastic", "adx25": "ADX 25+"}


def _rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def _sgn(x):
    return np.sign(np.nan_to_num(np.asarray(x, dtype=float))).astype(np.int8)


def compute(df):
    """{name: array} for every vote and filter a rule here can use, one value per bar of
    `df` (15-minute candles: Open High Low Close Volume, tz-aware index of bar STARTS)."""
    o, h, l, c, v = (df[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    t = df.index.tz_convert("UTC")
    day = t.normalize()
    out = {"candle": _sgn(c - o)}
    ha_c = ((o + h + l + c) / 4).to_numpy()
    ha_o = np.empty(len(df))
    if len(df):
        ha_o[0] = (o.iloc[0] + c.iloc[0]) / 2
        for i in range(1, len(df)):
            ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    out["ha"] = _sgn(ha_c - ha_o)
    pdc = c.groupby(day).last().shift().reindex(day).to_numpy()
    out["d1_trend"] = _sgn(c.to_numpy() - pdc)
    out["h1_trend"] = _sgn(c.ewm(span=80, adjust=False).mean() - c.ewm(span=200, adjust=False).mean())
    out["roc12"] = _sgn(c - c.shift(12))
    # RSI-2 (Connors): a sharp 2-candle dip (RSI2 < 10) while price is above its 200-candle average
    # is a buy, a sharp spike (> 90) below it a sell; the stochastic: %K(14), smoothed 3, vs 50.
    d = c.diff()
    rsi2 = 100 - 100 / (1 + _rma(d.clip(lower=0), 2) / _rma((-d).clip(lower=0), 2).replace(0, np.nan))
    s200 = c.rolling(200).mean()
    out["rsi2"] = np.where((rsi2 < 10) & (c > s200), 1, np.where((rsi2 > 90) & (c < s200), -1, 0)).astype(np.int8)
    k_ = 100 * (c - l.rolling(14).min()) / (h.rolling(14).max() - l.rolling(14).min()).replace(0, np.nan)
    out["stoch50"] = _sgn(k_.rolling(3).mean() - 50)
    out["vol_rising"] = (v.rolling(5).mean() > v.rolling(20).mean()).to_numpy()
    up_, dn_ = h.diff(), -l.diff()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _rma(tr, 14)
    pdi = 100 * _rma(up_.where((up_ > dn_) & (up_ > 0), 0.0), 14) / atr
    ndi = 100 * _rma(dn_.where((dn_ > up_) & (dn_ > 0), 0.0), 14) / atr
    adx = _rma(100 * (pdi - ndi).abs() / (pdi + ndi), 14)
    out["adx_rising"] = (adx > adx.shift(3)).to_numpy()
    out["adx25"] = (adx >= 25).to_numpy()                                   # a strong trend
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy()     # cfd_outcomes.atr14
    return out


_CACHE = {}                 # (instrument, last closed bar, its close, its volume, bars) -> compute() output


def evaluate(index_key, df, now=None):
    """The rule's reading on the last CLOSED candle of `df`, or None when this instrument
    has no rule. {ready, votes, filters, side (+1/-1/0), atr, close, bar_close, fresh}.

    A candle counts as closed only when `df` already holds the NEXT one (the forming candle):
    candles fetched a moment before a close end on a bar that was still forming when it was
    read, and acting on it would be acting on half a candle. Until the new candle shows up the
    reading waits. compute() runs once per closed candle and instrument - shared by every
    feed - not on every one-second recompute."""
    plan = config.cfd_rule(index_key)
    if not plan:
        return None
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if df is None or not len(df):
        return {"ready": False, "why": "no candles yet"}
    if df.index[-1] + BAR <= now:
        return {"ready": False, "why": "waiting for the new 15-minute candle's first prices"}
    closed = df.iloc[:-1]
    if len(closed) < 250:
        return {"ready": False, "why": f"only {len(closed)} closed candles - 250 needed"}
    key = (index_key, closed.index[-1], float(closed["Close"].iloc[-1]), float(closed["Volume"].iloc[-1]), len(closed))
    a = _CACHE.get(key)
    if a is None:
        a = {k: v[-1] for k, v in compute(closed).items()}
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = a
    votes = {k: int(a[k]) for k in plan["votes"]}
    filters = {k: bool(a[k]) for k in plan["filters"]}
    first = votes[plan["votes"][0]]
    side = first if first != 0 and all(x == first for x in votes.values()) and all(filters.values()) else 0
    bar_close = closed.index[-1] + BAR
    return {"ready": True, "votes": votes, "filters": filters, "side": side, "atr": float(a["atr"]),
            "close": float(closed["Close"].iloc[-1]), "bar_close": bar_close.isoformat(),
            "fresh": (now - bar_close).total_seconds() <= RULE_ENTRY_WINDOW_S}


def apply(rec, ev, index_key):
    """Write the rule's reading into a recommendation: the votes the Signal card shows, and -
    when it says buy or sell - the side, the stop and the one target a ticket freezes."""
    plan = config.cfd_rule(index_key)
    if not plan or ev is None:
        return rec
    info = {"label": plan.get("label", "Exness rule"), "stop_atr": plan["stop_atr"], "target_r": plan["target_r"],
            "ready": ev.get("ready", False), "why": ev.get("why"),
            "votes": [{"key": k, "name": LABELS.get(k, k), "vote": (ev.get("votes") or {}).get(k)} for k in plan["votes"]],
            "filters": [{"key": k, "name": LABELS.get(k, k), "ok": (ev.get("filters") or {}).get(k)} for k in plan["filters"]],
            "side": ev.get("side", 0), "bar_close": ev.get("bar_close"), "fresh": ev.get("fresh", False),
            "forward_test": plan.get("forward_test")}       # on trial, not proven - the card says so
    rec["rule"] = info
    # A CFD has no option: nothing of the engine's option fields may be frozen into the ticket.
    rec.update(suggested_strike=None, strike_swap=None, strike_taken=False, premium_targets=[None, None, None],
               premium_stop_loss=None, premium_source=None, live_ltp=None)
    side = ev.get("side", 0) if ev.get("ready") else 0
    spot = rec.get("spot")
    # The engine's room-to-run reward:risk (reach_to_risk / reach_points) is not this rule's: its own
    # is target over stop, fixed. Left in, the trade log's reward_risk column read the engine's 17.51
    # for a 0.75 trade (the user, 4 Oct 2026: "fix the journal reward risk") - as ai_desk does for
    # its own trades, the reading's columns describe THIS trade.
    if side and spot is not None and ev.get("atr", 0) > 0:
        R = plan["stop_atr"] * ev["atr"]
        tgt = plan["target_r"] * R
        rec.update(bias="BULLISH" if side > 0 else "BEARISH", option_type="CE" if side > 0 else "PE",
                   index_stop_loss=round(spot - side * R, 2), risk_points=round(R, 2),
                   index_targets=[round(spot + side * tgt * f / 3, 2) for f in (1, 2, 3)],
                   reach_to_risk=plan["target_r"], reach_points=None,
                   target_basis="rule", gate_targets=None, confidence="Rule",
                   action=f"{'BUY' if side > 0 else 'SELL'} - {info['label']}: every vote agrees on the 15-minute close")
    else:
        rec.update(bias="NEUTRAL", option_type=None, index_stop_loss=None, risk_points=None,
                   reach_to_risk=None, reach_points=None,
                   index_targets=[None, None, None], target_basis="rule", confidence="N/A",
                   action=("NO TRADE - WAIT (" + (ev.get("why") or "the rule's votes and filters do not all agree "
                                                  "on the last 15-minute close") + ")"))
    return rec
