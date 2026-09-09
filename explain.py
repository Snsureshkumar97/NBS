"""
explain.py — why the tool said what it said, in plain English
================================================================================
The right-hand ticket stub already shows WHICH indicators voted which way,
as arrows. An arrow tells you the conclusion but not the evidence: "▲ RSI"
doesn't tell you RSI is at 61, that 61 is inside the 50-75 band the tool
treats as bullish, or that at 76 it would have abstained instead.

This module turns one recommendation into sentences that quote the actual
numbers and the actual thresholds, so every line can be checked against the
chart sitting next to it. Nothing here computes anything or changes any
decision — it only describes a decision that has already been made.

Everything is derived from the recommendation dict built by
signal_engine.build_recommendation(), so the explanation can never drift
out of step with the logic: if the rule changes, the sentence changes with
it.
"""

import config


def _n(v, dp=0):
    """Index levels, with thousands separators."""
    if v is None:
        return "?"
    return f"{v:,.{dp}f}"


# ---------------------------------------------------------------------------
# One entry per indicator
# ---------------------------------------------------------------------------
def _trend_reason(tech):
    score = tech.get("trend_score", 0)
    close = tech.get("last_close")
    fast, slow = tech.get("ema_fast"), tech.get("ema_slow")
    f, s = config.EMA_FAST, config.EMA_SLOW
    if score > 0:
        text = (f"Price {_n(close)} is above the {s}-EMA ({_n(slow)}), and the "
                f"{f}-EMA ({_n(fast)}) is above it too. Both conditions have to "
                f"hold, so the averages are stacked in an uptrend.")
    elif score < 0:
        text = (f"Price {_n(close)} is below the {s}-EMA ({_n(slow)}), and the "
                f"{f}-EMA ({_n(fast)}) is below it too. Both conditions have to "
                f"hold, so the averages are stacked in a downtrend.")
    else:
        text = (f"The averages aren't stacked — {f}-EMA {_n(fast)} vs {s}-EMA "
                f"{_n(slow)}, price {_n(close)}. Price is on one side and the "
                f"EMAs disagree, which is what sideways looks like, so Trend "
                f"stays out of the vote.")
    return {"name": "Trend", "vote": score,
            "reading": f"EMA{f}/{s}", "text": text}


def _macd_reason(tech):
    score = tech.get("macd_score", 0)
    hist = tech.get("macd_hist")
    fa, sl, sg = config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL
    if score > 0:
        text = (f"MACD histogram is {hist:+g}. The {fa}/{sl} average is above its "
                f"{sg}-period signal line and pulling away, which means upward "
                f"momentum is still being added rather than just present.")
    elif score < 0:
        text = (f"MACD histogram is {hist:+g}. The {fa}/{sl} average is below its "
                f"{sg}-period signal line, so downward momentum is still being "
                f"added rather than just present.")
    else:
        text = "MACD histogram is flat at zero — momentum is balanced, no vote."
    return {"name": "MACD", "vote": score,
            "reading": f"{hist:+g}" if hist is not None else "", "text": text}


def _rsi_reason(tech):
    score = tech.get("rsi_score", 0)
    r = tech.get("last_rsi")
    if r is None:
        return {"name": "RSI", "vote": 0, "reading": "", "text": "RSI unavailable."}
    if score > 0:
        text = (f"RSI is {r:.0f}, inside the {config.RSI_BULL_MIN}-"
                f"{config.RSI_OVERBOUGHT} band the tool treats as bullish: strong "
                f"enough to mean buyers are in control, not so stretched that it's "
                f"about to snap back.")
    elif score < 0:
        text = (f"RSI is {r:.0f}, inside the {config.RSI_OVERSOLD}-"
                f"{config.RSI_BEAR_MAX} band the tool treats as bearish: weak "
                f"enough to mean sellers are in control, not so oversold that a "
                f"bounce is due.")
    elif r >= config.RSI_OVERBOUGHT:
        text = (f"RSI is {r:.0f}, above {config.RSI_OVERBOUGHT} — overbought. The "
                f"move up is real but too stretched to call a fresh entry, so RSI "
                f"abstains rather than voting bullish into a likely pullback.")
    elif r <= config.RSI_OVERSOLD:
        text = (f"RSI is {r:.0f}, below {config.RSI_OVERSOLD} — oversold. The move "
                f"down is real but too stretched to call a fresh entry, so RSI "
                f"abstains rather than voting bearish into a likely bounce.")
    else:
        text = f"RSI is {r:.0f}, sitting right at the midline — genuinely neutral."
    return {"name": "RSI", "vote": score, "reading": f"{r:.0f}", "text": text}


def _vwap_reason(tech):
    score = tech.get("vwap_score", 0)
    gap = tech.get("vwap_gap")
    vwap = tech.get("vwap")
    if score > 0:
        text = (f"Price is {gap:g} pts above VWAP ({_n(vwap)}). VWAP is the average "
                f"price everyone who traded today actually paid, so above it means "
                f"the average buyer is in profit — that's the side with the day.")
    elif score < 0:
        text = (f"Price is {abs(gap):g} pts below VWAP ({_n(vwap)}). VWAP is the "
                f"average price everyone who traded today actually paid, so below "
                f"it means the average buyer is underwater — sellers have the day.")
    else:
        text = f"Price is sitting exactly on VWAP ({_n(vwap)}) — neither side owns the day."
    return {"name": "VWAP", "vote": score,
            "reading": f"{gap:+g}" if gap is not None else "", "text": text}


def _pcr_reason(oi):
    if not oi or not oi.get("available"):
        return {"name": "PCR", "vote": None, "reading": "n/a",
                "text": ("No option-chain data is reaching the tool for this index "
                         "right now, so PCR has no vote. It is not counted against "
                         "the signal — a missing opinion never raises the bar.")}
    pcr = oi.get("pcr")
    score = oi.get("oi_score", 0)
    if score > 0:
        text = (f"Put/Call open-interest ratio is {pcr:.2f}, above 1.20 — far more "
                f"puts written than calls. Traders only sell puts when they expect "
                f"support below to hold, so heavy put writing reads as bullish.")
    elif score < 0:
        text = (f"Put/Call open-interest ratio is {pcr:.2f}, below 0.80 — far more "
                f"calls written than puts. Traders only sell calls when they expect "
                f"resistance above to hold, so heavy call writing reads as bearish.")
    else:
        text = (f"Put/Call open-interest ratio is {pcr:.2f}, inside the neutral "
                f"0.80-1.20 band. Option writers aren't leaning either way, so PCR "
                f"abstains. It sits in this band most of the day, which is normal.")
    return {"name": "PCR", "vote": score,
            "reading": f"{pcr:.2f}" if pcr is not None else "", "text": text}


# ---------------------------------------------------------------------------
def _gate_reason(rec):
    tech = rec.get("technical", {})
    adx = tech.get("adx")
    needed = rec.get("adx_needed", config.ADX_TREND_THRESHOLD)
    if adx is None:
        return {"ok": None, "text": "ADX unavailable."}
    if rec.get("adx_blocked"):
        return {"ok": False, "text": (
            f"ADX is {adx}, below the {needed} the tool requires. ADX measures how "
            f"STRONG a trend is, never which way it points. Below {needed} the market "
            f"is chopping, and indicators agreeing inside chop is usually coincidence, "
            f"not a move. The indicators lined up {rec.get('raw_bias','').lower()} here "
            f"and were overruled by this.")}
    if adx >= needed:
        return {"ok": True, "text": (
            f"ADX is {adx}, at or above the {needed} threshold — there is a genuine "
            f"trend in progress, strong enough that agreement between indicators is "
            f"likely to mean something rather than being noise.")}
    return {"ok": False, "text": (
        f"ADX is {adx}, below {needed} — weak trend, choppy conditions. Even if the "
        f"indicators did line up right now, this gate would veto the trade.")}


def _verdict(rec):
    bias = rec.get("bias")
    agree, dissent = rec.get("agree", 0), rec.get("dissent", 0)
    voting = rec.get("voting", 0)
    min_agree, max_dissent = rec.get("min_agree"), rec.get("max_dissent")
    strict = rec.get("strictness", config.SIGNAL_STRICTNESS)
    if bias in ("BULLISH", "BEARISH"):
        side = "CE (Call)" if bias == "BULLISH" else "PE (Put)"
        return (f"{agree} of the {voting} indicators that had an opinion agree, "
                f"{dissent} disagree. At '{strict}' strictness that needs at least "
                f"{min_agree} agreeing and no more than {max_dissent} against — met. "
                f"The trend gate passed. Signal: BUY {side}.")
    blockers = rec.get("blockers") or []
    if blockers:
        return "No trade, because: " + "  ".join(f"({i+1}) {b}" for i, b in enumerate(blockers))
    return "No trade right now."


def _levels(rec):
    """The stop and targets, and where each number came from."""
    out = []
    otype = rec.get("option_type")
    sl = rec.get("index_stop_loss")
    risk = rec.get("risk_points")
    tech = rec.get("technical", {})
    atr = tech.get("last_atr")

    if not otype or sl is None:
        return out

    if rec.get("sl_basis") == "swing_capped":
        swing = tech.get("last_swing_low") if otype == "CE" else tech.get("last_swing_high")
        out.append(
            f"STOP {_n(sl)} — the last swing ({_n(swing)}) is too far away to be a usable "
            f"intraday stop, so this is capped at {config.MAX_RISK_ATR_MULT}x ATR "
            f"({atr:.0f} pts) instead. Risk is {risk} pts. A stop that wide isn't one "
            f"you'd actually take, and it would also wreck the reward:risk test by "
            f"inflating what you're dividing by.")
    elif rec.get("sl_basis") == "swing":
        swing = tech.get("last_swing_low") if otype == "CE" else tech.get("last_swing_high")
        word = "lowest low" if otype == "CE" else "highest high"
        side = "below" if otype == "CE" else "above"
        out.append(
            f"STOP {_n(sl)} — placed just {side} the {word} of the last "
            f"{config.SWING_LOOKBACK} candles ({_n(swing)}), plus a small buffer of "
            f"{config.SL_BUFFER_ATR_MULT}x ATR. That level is on the chart because the "
            f"market actually turned there; a fixed-distance stop isn't. Risk is "
            f"{risk} pts.")
    else:
        out.append(
            f"STOP {_n(sl)} — the recent swing was too close to price to be usable, so "
            f"this falls back to {config.SL_ATR_MULT}x ATR "
            f"({atr:.0f} pts). Risk is {risk} pts.")

    basis = rec.get("target_basis")
    tg = rec.get("index_targets") or []
    exp = (rec.get("reach") or {}).get("range_expansion")
    exp_note = ""
    if exp and exp > 1:
        exp_note = (f" The day's usual range was widened {exp:g}x first, because ADX says "
                    f"this is a trending day and trend days routinely run past a normal "
                    f"day's range.")
    if basis == "market_reach":
        pcts = ", ".join(f"{int(f*100)}%" for f in config.REACH_FRACTIONS)
        out.append(
            f"TARGETS {_n(tg[0])} / {_n(tg[1])} / {_n(tg[2])} — set at {pcts} of the "
            f"{rec.get('reach_points')} pts the market can realistically still travel "
            f"today. That ceiling is the tightest of three limits, and here the binding "
            f"one is {rec.get('reach_reason')}. Reward:risk works out at "
            f"{rec.get('reach_to_risk')}:1.{exp_note}")
    elif basis == "risk_multiple":
        mults = " / ".join(f"{m:g}R" for m in config.RR_MULTS)
        out.append(
            f"TARGETS {_n(tg[0])} / {_n(tg[1])} / {_n(tg[2])} — {mults}, i.e. multiples "
            f"of the {risk} pts being risked. No option-chain data was available to "
            f"check whether the market can actually reach them.")
    elif basis == "not_reachable":
        out.append(
            f"NO TARGETS — only {rec.get('reach_points')} pts of room in this direction "
            f"({rec.get('reach_reason')}) against a {risk} pt stop. The setup is real; "
            f"the payoff isn't.")

    if rec.get("premium_source") == "live" and rec.get("live_ltp"):
        pt = rec.get("premium_targets") or []
        out.append(
            f"PREMIUM — the {rec.get('suggested_strike')} {otype} is trading at "
            f"{rec['live_ltp']}. Those index targets imply roughly {pt[0]} / {pt[1]} / "
            f"{pt[2]} on the option, using ~{config.APPROX_ATM_DELTA} delta. That is an "
            f"approximation and ignores time decay, which works against you the longer "
            f"the move takes.")
    return out


# ---------------------------------------------------------------------------
def explain(rec):
    """Full breakdown of one recommendation.

    Returns {"votes": [...], "gate": {...}, "verdict": str, "levels": [str],
             "headline": str}
    """
    if not rec:
        return {"votes": [], "gate": {"ok": None, "text": ""},
                "verdict": "No data yet.", "levels": [], "headline": "Waiting for data"}

    tech = rec.get("technical", {}) or {}
    oi = rec.get("option_chain", {}) or {}
    votes = [_trend_reason(tech), _macd_reason(tech), _rsi_reason(tech),
             _vwap_reason(tech), _pcr_reason(oi)]

    bias = rec.get("bias")
    if bias == "BULLISH":
        headline = f"BUY CE — {rec.get('suggested_strike')} Call"
    elif bias == "BEARISH":
        headline = f"BUY PE — {rec.get('suggested_strike')} Put"
    elif rec.get("adx_blocked"):
        headline = "No trade — trend too weak"
    elif rec.get("not_worth_it"):
        headline = "No trade — not enough room to profit"
    else:
        headline = "No trade — indicators not agreeing"

    return {"votes": votes, "gate": _gate_reason(rec), "verdict": _verdict(rec),
            "levels": _levels(rec), "headline": headline}
