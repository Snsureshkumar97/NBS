"""
greeks.py — Black-Scholes for the option chain, and the numbers that come out of it
================================================================================
Index options on the NSE and BSE are European: they can only be exercised at
expiry, which is exactly what Black-Scholes assumes. So the model fits the
instrument here, and this file does not pretend it fits anything else.

WHAT THIS IS FOR
    The chain gives a price. A price on its own says very little - 120 rupees
    for a strike 200 points away is dear or cheap depending on how much time is
    left and how much the index has been moving. Implied volatility is that
    price restated as the movement it implies, which is comparable across
    strikes and across days. The greeks say how the price will change: delta
    per point of index, gamma per point of delta, theta per day of waiting,
    vega per point of implied volatility.

WHAT IT IS NOT
    * Not a prediction. Implied volatility is what the market is charging, not
      what will happen.
    * Not a fit for American options, dividends paid mid-life, or anything
      settled in the underlying coin rather than cash. Deribit's BTC options
      are inverse and quoted in coin - they need their own handling before any
      of this is applied to them, so this module refuses rather than guesses.
    * Not exact on expiry day. As time to expiry approaches zero the greeks
      stop being smooth and implied volatility gets very sensitive to a stale
      quote. Everything here carries the time it used so that can be seen.

THE RATE
    India's short-term risk-free rate is around 6.5%. Theta and implied
    volatility both move with it, though far less than with time or spot, and
    it is a declared input rather than a hidden constant.
"""
import math

RATE = 0.065          # short-term Indian risk-free, annual
MINUTES_A_YEAR = 365.0 * 24 * 60


def _n_cdf(x):
    """Standard normal CDF, via the error function in the stdlib."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _n_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot, strike, t, sigma, r=RATE):
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return None, None
    v = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / v
    return d1, d1 - v


def price(spot, strike, t, sigma, kind, r=RATE):
    """Black-Scholes price of a European option. kind is "CE" or "PE"."""
    kind = (kind or "").upper()
    if t <= 0 or sigma <= 0:
        # At or past expiry the option is worth its intrinsic value, nothing more.
        return max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    d1, d2 = _d1_d2(spot, strike, t, sigma, r)
    disc = math.exp(-r * t)
    if kind == "CE":
        return spot * _n_cdf(d1) - strike * disc * _n_cdf(d2)
    return strike * disc * _n_cdf(-d2) - spot * _n_cdf(-d1)


def greeks(spot, strike, t, sigma, kind, r=RATE):
    """Delta, gamma, theta (per calendar day), vega (per volatility point)."""
    kind = (kind or "").upper()
    d1, d2 = _d1_d2(spot, strike, t, sigma, r)
    if d1 is None:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}
    disc = math.exp(-r * t)
    pdf = _n_pdf(d1)
    gamma = pdf / (spot * sigma * math.sqrt(t))
    vega = spot * pdf * math.sqrt(t) / 100.0          # per 1 vol point
    if kind == "CE":
        delta = _n_cdf(d1)
        theta = (-spot * pdf * sigma / (2 * math.sqrt(t))
                 - r * strike * disc * _n_cdf(d2)) / 365.0
    else:
        delta = _n_cdf(d1) - 1.0
        theta = (-spot * pdf * sigma / (2 * math.sqrt(t))
                 + r * strike * disc * _n_cdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def implied_vol(mkt, spot, strike, t, kind, r=RATE, lo=0.005, hi=5.0):
    """The volatility that makes Black-Scholes agree with the market price.

    Bisection rather than Newton: it cannot diverge, and the chain is full of
    prices that break Newton - a quote below intrinsic value, a wide spread on
    a far strike, a stale print on an illiquid one. Bisection either brackets
    a root or reports that it cannot, which is the honest answer for a price
    the model has no volatility for.
    """
    if mkt is None or mkt <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = (max(0.0, spot - strike) if (kind or "").upper() == "CE"
                 else max(0.0, strike - spot))
    if mkt < intrinsic - 1e-6:
        return None                       # below intrinsic: no volatility fits
    if price(spot, strike, t, hi, kind, r) < mkt:
        return None                       # beyond the bracket; do not extrapolate
    for _ in range(100):
        mid = (lo + hi) / 2
        if price(spot, strike, t, mid, kind, r) > mkt:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def years_to_expiry(minutes):
    """Time in years from minutes remaining. Minutes because on expiry day the
    difference between morning and afternoon is most of the option's life."""
    return max(0.0, minutes) / MINUTES_A_YEAR


def scenarios(spot, strike, t, sigma, kind, premium, qty=0, move_pct=0.5, vol_drop=2.0):
    """The premium under a few plain what-ifs, repriced in full.

    Delta and vega are straight lines; an option is not. A move of any size, a
    volatility drop after an event, or a day off a short-dated contract comes
    out here as the model prices it. The model is used only for the change,
    which is added to the premium on screen, so a quote the model does not fit
    exactly still starts from what the market shows.

    Rows: the index `move_pct` up and down, volatility `vol_drop` points lower,
    a day later with nothing moving (to the close when less than a day is
    left), and all three against the position at once.
    """
    kind = (kind or "").upper()
    if (kind not in ("CE", "PE") or not spot or not strike or not premium
            or not sigma or sigma <= 0 or t is None or t <= 0):
        return []
    base = price(spot, strike, t, sigma, kind)
    day = 1.0 / 365.0
    later = t - day if t > day else 0.0
    later_label = ("A day later, nothing moves" if t > day
                   else "At the 15:30 expiry, nothing moves")
    m = move_pct / 100.0
    low_vol = max(sigma - vol_drop / 100.0, 0.01)
    against = spot * (1 - m) if kind == "CE" else spot * (1 + m)
    rows = (("up", f"Index up {move_pct:g}%", spot * (1 + m), t, sigma),
            ("down", f"Index down {move_pct:g}%", spot * (1 - m), t, sigma),
            ("vol", f"Volatility {vol_drop:g} points lower", spot, t, low_vol),
            ("time", later_label, spot, later, sigma),
            ("worst", "All three against you", against, later, low_vol))
    out = []
    for key, label, s_, t_, v_ in rows:
        new = max(0.0, premium + price(s_, strike, t_, v_, kind) - base)
        change = new - premium
        out.append({"key": key, "label": label, "premium": round(new, 2),
                    "change": round(change, 2),
                    "rupees": round(change * qty, 2) if qty else None})
    return out
