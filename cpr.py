"""
cpr.py — the Central Pivot Range, one definition for the page and for cpr_study.py
================================================================================
The user, 21 Oct 2026: "what is CPR how is it usefull when we are doing option trading", then "show it and give simple
explanation" and "test it as well". From the previous finished session's high H, low L and close C:
    pivot P = (H + L + C) / 3,   BC = (H + L) / 2,   TC = 2P - BC      (the band runs from the lower of BC/TC to the higher)
Its WIDTH against the average width of the previous WIDTH_LOOKBACK sessions' CPRs says what traders expect of the day:
    narrow  (<= NARROW x the average)  - a trending day, a bigger one-way move
    wide    (>= WIDE x the average)    - a sideways, range-bound day
    normal  otherwise
And the price against it: above the band (buyers in control), below it (sellers), inside (undecided).
Display only: nothing here takes part in a trade (cpr_study.py measures whether it should).
"""
WIDTH_LOOKBACK = 10
NARROW, WIDE = 0.6, 1.4


def band(h, l, c):
    """(pivot, bottom, top) of the CPR built from one session's high, low and close."""
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = 2.0 * p - bc
    return p, min(bc, tc), max(bc, tc)


def levels(sessions, now=None):
    """The CPR for the session after the last of `sessions` - a list of (date, high, low, close) of FINISHED sessions,
    oldest first - or None with fewer than 4. {"session" (the one it was built from), "pivot", "bc", "tc", "width",
    "avg_width", "ratio", "kind" ("narrow" | "normal" | "wide" | None), "where" ("above" | "below" | "inside" | None)}."""
    if not sessions or len(sessions) < 4:
        return None
    d, h, l, c = sessions[-1]
    p, lo, hi = band(float(h), float(l), float(c))
    widths = []
    for _, hh, ll, cc in sessions[-WIDTH_LOOKBACK - 1:-1]:
        _, a, b = band(float(hh), float(ll), float(cc))
        widths.append(b - a)
    width = hi - lo
    avg = sum(widths) / len(widths) if widths else None
    ratio = (width / avg) if avg else None
    kind = None if ratio is None else "narrow" if ratio <= NARROW else "wide" if ratio >= WIDE else "normal"
    where = None
    if now is not None:
        where = "above" if now > hi else "below" if now < lo else "inside"
    return {"session": str(d), "pivot": round(p, 2), "bc": round(lo, 2), "tc": round(hi, 2), "width": round(width, 2),
            "avg_width": round(avg, 2) if avg else None, "ratio": round(ratio, 2) if ratio is not None else None,
            "kind": kind, "where": where}


def sessions_from(df, before=None):
    """[(date, high, low, close)] per session from intraday candles (index in IST), oldest first; with `before` (a
    date), only the sessions before it."""
    days = {}
    for ts, hi, lo, cl in zip(df.index, df["High"], df["Low"], df["Close"]):
        d = ts.date()
        if before is not None and d >= before:
            continue
        if d not in days:
            days[d] = [float(hi), float(lo), float(cl)]
        else:
            x = days[d]
            x[0] = max(x[0], float(hi))
            x[1] = min(x[1], float(lo))
            x[2] = float(cl)
    return [(d, *v) for d, v in sorted(days.items())]
